import hashlib
import json
import re
import shlex
import sys
import traceback
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from drive import slack
from drive.state import DRIVE_TOOLING, brief_path, communicator_lane, FINISHED_JOB_STATES, FREED, NOT_STARTED, REPO_RULES, ROLES, SLOT_WAIT, TOOLING_DETAILS_RULING, VERSION, alias, carriers, delivered, exempted, following, holding_job_lane, kept_record, lane_death, launch_in_flight, may_tell, now, open_orders, orphan_stands, parse_time, part_jobs, pass_due, passes, pr_key, pr_url, project_lane, promise_thread, repo_rules, resumed, session_lane, standing_passes, title, unstarted, unstarted_builder, untracked_carriers, work

SLACK_TOOLS = "mcp__plugin_cc-slack_cc-slack__"
REWORKING = "no card while its builder reworks it to his words, until its READY says how they are met"
CCI_TOOLS = "mcp__plugin_cc-inbox_cci__"
REACHING = (
    "Post, read and close records with the cci MCP tools while that server is up, `cci post` only while it is gone;"
    " while cci is down, SendMessage `<drive>-<role>` after ListAgents: a disconnect never excuses a message."
)
RECORDING = (
    "Every record you send goes on the drive's record tool: owner for his words, answer with re <seq> for a session's"
    " record you answer, handoff and defect. Make no other cci call, a cci tool or a `cci` command, but `cci drive use`,"
    " since a record's body is the reader's to read; while cci is down, SendMessage `<drive>-<role>` after ListAgents: a"
    " disconnect never excuses a message."
)
PREVENTED = "A post telling him the drive failed says how that is prevented: the tool change and its order or PR."
STAMPS = (
    "What he reads, a say post, a card or a POST or ready record to the communicator, names when a job started as"
    " {started:<job>} and when a record went out as {at:<seq>}, never a clock time you type, and writes a time a tool or query printed,"
    " such as a GitHub check's, in Pacific on the line linking it or in backticks as it printed it, a log's UTC stamp such as 09:15:42Z always in Pacific, a release's, build's or deploy's start on the line linking that"
    " build or tool, so its first write passes the check that refuses a typed one; a time after the write passes, naming its day when it falls on another, as \"6:54 AM tomorrow\", and a"
    " SendMessage, a defect or any other record between sessions quotes times as it needs."
)
COMMUNICATOR_RULES = (
    (
        "You are his front, as a root session working for him is: his messages reach you, and you answer each yourself in"
        " the thread he wrote in, from the whole thread you hold, by his words \"it needs to be just as fast and fluid"
        ' asking me things and making the right judgement calls as you would". He never reads this terminal, which has no'
        " AskUserQuestion, so everything for him goes to Slack through say, and each turn ends on the one word Sent."
    ),
    (
        "Every message of his gets a real answer posted in the same turn: a simple question in about 10 seconds, read then"
        " say with no step between, and anything else within 60 seconds, from what you hold and a quick read: what it is,"
        " what you found, and what happens next and who does it. A work request is no exception: the answer is the read plus"
        " the plan, never only \"sent\". A Stop hook refuses to end a turn that left a message of his with no post in its"
        ' thread. Read every new message against the whole thread first: a ruling there, such as "anything with the matia'
        ' replica is fine", ends a trace at once.'
    ),
    (
        "What you hold is what the drive already keeps: `drive status <o, j, PR or words>`, under a second, never a bare"
        " `drive status`, which reads every open order for most of a minute; your channel's brief, the file named below,"
        " which the watch rewrites each pass with its open orders, what waits on him and the releases at work, and in a"
        " priority project's channel its canvas, which holds what each release is and where it stands; and the thread. You"
        " never run bk, tools/ci or git, or read a transcript, yourself. When those do not hold what he asks, post what they"
        " do hold as your answer now and start one Sonnet subagent with the Agent tool in the background, briefed to make no"
        " advisor call, to batch GitHub reads into one `gh api graphql` query and to read `drive status <order>`; post its"
        " finding as a follow-up in the thread when it returns. The watch posts the order's POST report in his thread itself."
    ),
    (
        "Progress on an answer that takes more than a few seconds goes in the thread's status, slack_dm_status with his"
        ' thread\'s ts, such as "Reading the plan\'s RDS change", never a post, and an answer from what you hold needs no status at all. A post is the answer or a question, with no placeholder, no ack and no "next line by"'
        " promise. Chatter (\"ok\", \"thanks\") gets a +1 react."
    ),
    (
        "Communicating is your whole job, by his words \"its sole job is to communicate, it's unacceptable that it's not perfect at"
        ' this": every post makes his life easier, read once on his phone, as "How to talk to him" in communicator.md shows with his'
        " gold-standard post. The answer first in one plain sentence; past about 60 words, short parts under bold headings, lists as"
        " bullets; every PR, build, card or doc linked on its words, never a bare URL but an alert's own link alone on its line;"
        " times in Pacific; plain words for what happens to his work, never the drive's machinery or which read backs a claim."
        " Reproduce what you refer to, a card's question and options, a release's stage, with its link or its card drawn under the"
        " post, never leaving him to find it in another thread. Use say's blocks, Block Kit header, section, fields, context and"
        " divider, for a post he scans, and a visual, such as a chart of a release's stages, when a picture shows progress at a"
        " glance. In a project or priority channel a new update, release step or decision is a channel message at the root, and only an answer to his message, or a follow-up on its subject with in_thread, goes in his thread. One post per thing, a correction an edit. say runs `cc-slack check-post` on every post of yours, fixes"
        " what is mechanical, a UTC stamp, a zone label, a bare link, a long paragraph, and posts it in the same call, warning you of"
        " the rest and of one quick Jev read against this standard: nothing comes back to rewrite, so write it right the first time."
    ),
    (
        "A decision only he can make is a card from you within a minute: 2 to 4 options with your recommendation first."
        " Work, a build, release, fix or PR, a question proposing work such as \"why not just X?\" included, goes to the"
        " coordinator, or the seat holding its order, an alert's straight to the seat owning that area by its order attr, as his owner record on the drive's record tool passing his message"
        " along whole, since the session that acts on it reads it and decides, and `about` naming what it is about; you"
        " never wait on that session to answer a read."
    ),
    (
        "A question-card answer (slack.action with waits: true) and an Undo get no record: cc-slack closed the card and"
        " its card command posted his answer. A payload without `recorded: true` is a card command that missed it: one"
        " defect record quoting the payload. A PR-card tap (merge:) gets nothing, since cc-slack records it."
    ),
    (
        "The watch posts each POST report itself, so a report reaching you is context with nothing to post; an `ANSWER"
        " THIS:` note from lane watch hands you a report the watch could not post, with why, to post in the thread it names"
        " citing it with record: <seq> on say, or a message of his gone unanswered for 10 minutes, by his ruling \"get rid of handlers\"."
    ),
    (
        f"Drive tooling, {DRIVE_TOOLING}, is the meta session's alone, never yours or the coordinator's, so you start no"
        " reader, designer or builder for it: a defect you hit, a hook refusing wrongly or a"
        " refusal or queue holding back work he waits on included, each get one record call that turn, kind defect, with"
        " the refusal quoted, and every owner record names about, drive for drive"
        " tooling, his ruling or feedback on it included, and his work for the rest; the record tool sends a defect and an"
        " owner record about the drive to the meta session alone, adding first the order its order field names, which tells"
        " the coordinator what changes for its work,"
        " and a handoff, a step only the session holding an order takes on it, to that session by its order attr."
    ),
    RECORDING,
    (
        "After a compaction or a restart, your first call is the `ToolSearch` below; then answer each message of his"
        " whose answer has not gone out."
    ),
    "Post nothing on your own clock: his DM gets only the answer to a message of his, a card he must answer, a milestone he acts on and an incident's line; progress stays in the thread's status, on the dashboard and in the records.",
)


COORDINATOR_RULES = (
    (
        "You decide and act: what an order means, what to start and in what order, what to hold or drop, which"
        " reading of his words is right, and a release his words cover. Report what you chose and why in one line,"
        " and his later word steers it. Nothing waits for a quote of his words, an answer id, a confirmation or"
        " another session's say-so: a wrong reading costs one correction from him, a round trip costs him the wait"
        " every time."
    ),
    (
        "A decision only he can make, a dangerous step included, is asked within a minute as one card with 2 to 4"
        " options and a recommendation, by whoever hits it, never deliberated between lanes. His ruling is applied as"
        " worded, never narrowed: a plan dropping something his words included is wrong, and you say which thing."
    ),
    (
        "Post a record only when one other lane must act on it now: a handoff, ask, answer, decide, defect or report"
        " to that one lane, never progress, state or a relayed copy, and several lanes only for an incident or"
        " blocker naming each lane's step. A project coordinator sends root one report opening UP, no reply expected,"
        " when it acts on his words, starts, cancels or holds a release, takes or hands a staging slot, or changes"
        " an order's plan, and root never asks what those lines answer. Messages reach you only between tool calls:"
        " run anything past about 30 seconds in the background and end your turn to wait."
    ),
    (
        "You have no AskUserQuestion. His DM gets the answer to a message of his, a card drive job ask draws for a"
        " question that is truly his, a milestone the watch posts itself from the drive's rows (a fix he reported live,"
        " a release started, finished or failed, a PR of his landed) and an incident's line. A line he gets goes to"
        " communicator@<drive> as a report opening POST, a milestone the watch does not post marked --attr"
        " milestone=true; any other report is progress, kept in the records and the dashboard. Write a report as the"
        " facts of the post he reads: its first line says what changed for him and each thing he could open has its"
        " URL beside it. A POST names what it finishes, --attr close=<o>, part=<o>:<part> or progress=true. What he"
        " reads names things by their proper technical terms and no drive id he cannot open. "
        + STAMPS
        + " "
        + PREVENTED
    ),
    REACHING,
    (
        "One limit holds whatever you decide: nothing dangerous lands or releases while he is away, his last word or"
        " tap over 15 minutes old, since it can break customers' runs or lose their data. The lander holds a merge"
        " changing what the repository's root AGENTS.md calls dangerous until his next word, and the release gates"
        " and the landing_guard hook refuse a production start, resume or approve but a hotfix of PRs"
        " changing nothing dangerous; a rollback runs whenever it is prudent. A production failure meanwhile pages"
        " him: the watch pages once for each incident opened since he was last heard, and drive page-owner \"<why>\""
        " pages for one no incident holds yet, never for news or a question."
    ),
    (
        "Give each order to one background job that owns its deliverable end to end, since work in your own turn"
        " holds every event behind it. A brief states the goal, what done looks like and the facts the job cannot"
        " find itself; drive manual jobs has its shape. A job decides the forks it meets and names them in its"
        " return."
    ),
    (
        "The pages are reference: drive manual coordinator <row> prints the row of an event line, record or job"
        " result, and drive manual verbs <verb> a verb's flags, for a line or verb you do not know."
    ),
    (
        "A tool's refusal is information. When one holds back his work, do the work another way and send one record,"
        " kind defect, quoting it, which reaches the meta session, since drive tooling is its to fix."
    ),
    (
        "Tell him a state you checked this turn, or say it is unverified: read a job's transcript before you call it"
        " stuck or done. Read his message in Slack when you need his exact words."
    ),
)
META_RULES = (
    (
        "He never reads this terminal, which has no AskUserQuestion, and you post nothing to Slack, since his reply"
        " under a post reaches only the session that posted it. His DM gets only the answer to a message of his, a card,"
        " a milestone the watch posts from the drive's rows and an incident's line: the answer goes to communicator@<drive>"
        " as a report record opening POST, its first line what changed for him and each thing he could open with its URL"
        " beside it, --attr close=<o> on the line that finishes a tooling order, and a question for him is kept with drive"
        " job ask, which draws his card on it; the communicator posts that report. Progress goes to no one. "
        + STAMPS
        + " "
        + PREVENTED
    ),
    REACHING,
    (
        f"You hold drive tooling alone, {DRIVE_TOOLING}, each piece of work a background job you start, and each change"
        " folds a new check, step, hook, flag or verb into what exists, or deletes one, before adding one, by his words"
        ' "in geernal keeps implifying all this tuff". Your jobs'
        " decide every detail of the tooling and build its recommended option, asking him only a high-level preference: what"
        " reaches him or when, a post or card's shape, a tap or gate he owes, a cost he pays, a ruling of his lifted, or what"
        f" the drive takes on, by his ruling {TOOLING_DETAILS_RULING}, and drive job ask keeps a question Opus reads as a detail"
        " decided, drawing no card. You never"
        ' touch his product orders, their jobs or PRs: drive order add refuses you an order not opening "Drive tooling:", and job'
        " add an order the coordinator holds."
    ),
    (
        "You decide and act on tooling: what a defect or ruling means, what to build and in what order. Report what"
        " changed in one line, and his later word steers it. The pages are reference: drive manual meta <row> prints"
        " the row of a record or job result you do not know. Keep your own turn short, since work in it holds every"
        " record behind it."
    ),
)
RULES = {"communicator": COMMUNICATOR_RULES, "coordinator": COORDINATOR_RULES, "meta": META_RULES}
OTHERS = {"communicator": ("coordinator", "meta"), "coordinator": ("communicator", "meta"), "meta": ("communicator", "coordinator")}
JOB = {
    "communicator": "you talk to him",
    "coordinator": "you run his orders, jobs, events, landings and installs",
    "meta": f"you hold drive tooling, {DRIVE_TOOLING}",
}
REPROS_SHOWN = 5
FILED = "The projects and workstreams in use, which a new order reuses where it fits, one home a subject, since drive order add and set refuse a name other orders file only as the other of the two: drive order unfiled lists each pair."
WHOLE = sys.maxsize
RULE_NUMBER = re.compile(r"^\d+\. ")
REPO_RULES_HEADING = f"The rules of the repository this drive works in, which its {REPO_RULES} holds at its default branch's head, and every session and job of every drive working in it follows:"
UNLISTED_TOOL_LINE = (
    "A drive tool these rules name that your session does not list, such as job, is one an install added"
    " since its drive MCP server last listed its tools: pipe the tool's fields as JSON to drive tool <name> in Bash"
    " until a call of any drive tool relists them."
)
FIRST_CALL = {
    "communicator": (
        "Your first call now, before anything else, loads the tools your turn needs: ToolSearch"
        " select:mcp__plugin_long-running_drive__say,mcp__plugin_cc-slack_cc-slack__slack_dm_status,"
        "mcp__plugin_cc-slack_cc-slack__slack_thread,mcp__plugin_cc-slack_cc-slack__slack_react,"
        "mcp__plugin_long-running_drive__record,mcp__plugin_long-running_drive__status,"
        "mcp__plugin_long-running_drive__order,mcp__plugin_long-running_drive__job,mcp__plugin_long-running_drive__page"
    ),
    "coordinator": (
        "The watch runs as its own process and posts the lines each stage of its pass makes for you over cci as one"
        " record, a handoff when any line asks you to act now, else evidence, at most one record every ten minutes holding all it made but his tap, a due time, a move of his orders or a stopped watch, which come at once, and only lines that ask a step, each said once: a line your manual says Nothing for never reaches you; a record of"
        " several lines counts them by kind and carries each in its body, read whole; arm no Monitor for it, and run"
        " no drive watch for a WATCH stopped line, since its launch agent starts it again. A compaction keeps"
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
    "communicator": "Everything else is on disk, read on demand: drive status <id or words>, your brief, drive job report <j>, and his thread with slack_thread.",
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
        "Each subagent of yours still out: one line, `<his thread ts> <agent id> <his ask in at most 12 words>`.",
        "Each message of his not answered yet: `<his thread ts> <permalink> <his words verbatim>`.",
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


def raised(error: Exception) -> str:
    """An error the drive did not expect, named by its type and the innermost frame of the drive's own code it was raised through, then its words, so the line alone says which line to read."""
    frame = next(frame for frame in reversed(traceback.extract_tb(error.__traceback__)) if Path(frame.filename).resolve().parent == Path(__file__).resolve().parent)
    where = f"{type(error).__name__} at {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
    return f"{where}: {cut(str(error), 200)}" if str(error) else where


def unread(words: list[str], failed: str | None = None) -> str:
    """What a job's line says of the texts its worker never got, which the job holds for its next worker: those it ended without reading, or those whose delivery failed as failed says."""
    count = "1 text" if len(words) == 1 else f"{len(words)} texts"
    lead = f"delivery of {count} to its worker failed ({failed})" if failed else f"it never read {count}"
    return f'{lead}, held for its next worker: "{cut(words[0], 80)}"'


def clock(moment: datetime, seconds: bool = False) -> str:
    return moment.strftime("%-I:%M:%S %p" if seconds else "%-I:%M %p")


def when(iso: str, seconds: bool = False) -> str:
    moment = parse_time(iso)
    if moment.date() == now().date():
        return clock(moment, seconds)
    return f"{moment.strftime('%b %-d')} {clock(moment, seconds)}"


def owner_reading(owner: dict) -> str | None:
    """Where he reads, from the moment the owner row's away holds: away from the moment he said so, else from an hour after he was last heard; none for a drive that never heard him."""
    if not (away := owner["away"]):
        return None
    heard = when(owner["heard"])
    if away == owner["heard"]:
        return f"away since he said so at {heard}"
    if now() >= parse_time(away):
        return f"away since {when(away)}: last heard {heard}, an hour with no word or tap"
    return f"here until {when(away)}: last heard {heard}, and an hour with no word or tap reads him away"


def start_now(prompt: str) -> str:
    """The one wording of a line naming a briefed builder or reader nothing holds: the watch starts its agent, as its own claude --bg session on this Mac by drive job start or on a Sprite by drive job launch, so its reader starts none and a text to it goes by drive job resume."""
    brief = prompt.removeprefix("Read your brief first: ")
    return f"the watch starts its agent on its brief {brief}, so start no agent for it; drive job resume reaches it"


def builder_lines(name: str, data: dict, builder_ids: list[str]) -> list[str]:
    """When to start each builder the drive added itself, beside a reader, for a change a design or builder listed or on a staging test, by the one view.start_refusal drive job launch and the agent_brief hook read, on the watch's FREE line, which names its brief, once its last wait ends, or now on the prompt its brief starts it on, by start_now, each briefed here by cli.write_brief, which takes the drive's lock, so its caller says these once its own save landed, but one waiting only on readers, by unstarted_builder, whose return briefs it on their research; a builder whose brief was refused says why and how to brief it."""
    from drive import cli, view

    lines = []
    for builder_id in builder_ids:
        builder = data["jobs"][builder_id]
        named = ", ".join(json.dumps(part, ensure_ascii=False) for part in builder["parts"])
        try:
            started = None if unstarted_builder(data, builder) else cli.write_brief(name, builder_id, {})[0].splitlines()[0]
        except cli.VERB_ERRORS as error:
            lines.append(f"{builder_id} builder on {named} has no brief: {error}; brief it with drive job brief {builder_id}, then start its agent on the line that prints")
            continue
        if view.start_refusal(data, builder_id, True):
            lines.append(f"{builder_id} builder on {named} waits on {', '.join(builder['waits'])}: start no agent for it until the watch's FREE line says its last wait ended")
            continue
        lines.append(f"{builder_id} builder on {named}: {start_now(started)}")
    return lines


def job_prs(drive: dict, job_id: str) -> dict[str, dict]:
    return {key: pr for key, pr in drive["prs"].items() if pr["job"] == job_id}


def prs_by_job(drive: dict) -> dict[str, list[str]]:
    jobs: dict[str, list[str]] = {}
    for key, pr in drive["prs"].items():
        jobs.setdefault(pr["job"], []).append(key)
    return jobs


def order_line(drive: dict, order_id: str, order: dict, limit: int = 120, thread: tuple[str, str] | None = None) -> str:
    where = f", its lines go in {thread[0]} thread {thread[1]}" if thread else ""
    return f"{order_id} {order['state']}{' incident' if order['incident'] else ''}{where}{reporter_said(drive, order)}: {cut(order['text'], limit)}"


def reporter_said(drive: dict, order: dict) -> str:
    """Each place the bug the order fixes was reported, or the question it answers asked, and whether that thread heard it, is owed it now, or hears it once its fix is proven live or its answer posted, read from the order keeping that thread's record when another does."""
    return "".join(reporter_heard(drive, order, *kept_record(drive, held)) for held in order["reporters"])


def reporter_heard(drive: dict, order: dict, keeper: str | None, reporter: dict) -> str:
    if order["kind"] == "answer" and reporter["told"]:
        return f", asked in {reporter['link']}, told the answer in {reporter['told']}"
    if order["kind"] == "answer":
        owed = "owed its answer now, since it is posted" if may_tell(drive, order) else "which hears the answer once it is posted"
        return f", asked in {reporter['link']}, {owed}"
    kept = f" under {keeper}" if keeper else ""
    if reporter["told"] is None:
        fix = f"{keeper}'s fix" if keeper else "its fix"
        owed = f"owed its fix-live note{kept} now, since {fix} is proven live" if may_tell(drive, drive["orders"][keeper] if keeper else order) else f"which hears it is fixed{kept} once {fix} is proven live"
        return f", reported in {reporter['link']}, {owed}"
    return f", reported in {reporter['link']}, told it is fixed in {reporter['told']}{kept}"


def incident_line(order_id: str, order: dict) -> str:
    return f"{order_id} {order['state']} incident: {order['text'][:80]}; {order['note'] or 'no record'}"


def pr_line(drive: dict, key: str, card: str | None) -> str:
    from drive import view

    pr = drive["prs"][key]
    owed = None if exempted(pr) else REWORKING if pr["rework"] else "wants tap" if pr["wants_tap"] else None
    tap = pr["tap"]["label"] if pr.get("tap") else owed or "no tap needed"
    tap += f", {card or 'his card never drawn'}" if card or not pr.get("tap") and owed == "wants tap" else ""
    exempt = f", exempt: {pr['exempt']}" if pr.get("exempt") else ""
    port = f" of #{pr['port_of']}" if pr.get("exempt") == "pure port" and pr.get("port_of") else ""
    waits = f", waits on {', '.join(pr['waits'])}" if pr.get("waits") else ""
    held = ", held by its hold label" if pr["hold_label"] else ""
    title = f' "{cut(pr["title"], 80)}"' if pr["title"] else ""
    return f"{key}{title} {pr['state']}, checks {pr['checks']}{landed_words(pr)}, review {pr['review']}, {tap}{held}{exempt}{port}{waits}{design_words(pr)}{stage_words(pr)}; {view.staging(drive, key)}"


def landed_words(pr: dict) -> str:
    """On a merged PR whose checks fail, the lander's merge of it as its outcome says it, which names each failing check it merged past as the default branch's, so its red never reads as its own; none on any other PR, or one the lander did not merge."""
    outcome = (pr["lander"] or {}).get("outcome") or ""
    if pr["state"] != "merged" or pr["checks"] != "fail" or not outcome.startswith("merged"):
        return ""
    return f" (the lander {outcome.partition('; ')[0]})"


def design_words(pr: dict) -> str:
    """The answers of his the PR cites for its design, as the check reads them, each by its id or link, or what the PR cites while the check has read none of it; never one cc-notes only ranked near the words of a card it cites."""
    if pr.get("answers"):
        return f", design answers {', '.join(answer.partition(', ')[0] for answer in pr['answers']['cited'])}"
    return f", design {cut(' '.join(pr['design'].split()), 80)}" if pr.get("design") else ""


def stage_words(pr: dict) -> str:
    """The PR's stage as stage.of named it, and the head and time of the GitHub read it stands on; none on a PR no longer open, whose row keeps the stage of its last open read while its state, which pr_line names, says all that is left."""
    if pr["state"] != "open" or not (now := pr.get("stage")):
        return ""
    since = f" since {when(now['since'])}" if now["since"] else ""
    read = f"; at {pr['head'][:7]} as GitHub read it at {when(pr['required']['read'])}" if pr.get("required") and pr["required"].get("read") else ""
    return f"; {now['name']}{since}, owed by {now['owner']}: {now['why']}{read}"


def job_line(drive: dict, job_id: str, job: dict, limit: int = 100) -> str:
    if job.get("where") == "remote":
        handle = f" remote dispatch {job['dispatch']}" if job.get("dispatch") else " remote"
    else:
        handle = f" agent {job['agent']}{agent_elsewhere(drive, job)}" if job.get("agent") else ""
    repro = f"; repro {job['repro']}" if job.get("repro") else ""
    carries = f"; {job['order']}'s job, carrying " + ", ".join(job["carries"]) if job.get("carries") else ""
    built = f" on {job['branch']}" if job["state"] == "built" else ""
    slot = f"a builder slot on this Mac, {job['in_line']}" if job.get("in_line") else "a builder slot on this Mac"
    waits = f"; waits on {', '.join(slot if wait == SLOT_WAIT else wait for wait in job['waits'])}" if job.get("waits") else ""
    shown = f"launching since {when(job['launching'])}" if launch_in_flight(drive["name"], job_id, job) else NOT_STARTED if unstarted(job) else f"lane dead since {when(dead['at'])} ({dead['why']})" if job["state"] == "running" and (dead := lane_death(job)) else job["state"]
    woken = f"; resumed {when(woke)} by a message after its last line" if (woke := resumed(job)) else ""
    held = "".join(f'; holds to deliver: "{cut(kept["words"], 80)}"' for kept in job["undelivered"])
    queued = "".join(f'; queued in its worker\'s mailbox, not yet seen read: "{cut(sent["words"], 80)}"' for sent in job["queued"])
    return f"{job_id} {job['kind']} {shown}{built}{handle}: {cut(job['goal'], limit)}{repro}{carries}{waits}{woken}{held}{queued}"


def agent_elsewhere(drive: dict, job: dict) -> str:
    """For an unfinished job whose agent on this Mac a session other than the one holding the job started, as when its order moved to another coordinator session, that session, whose SendMessage alone reaches the agent, and what drive job resume does with a text for it."""
    if job["state"] in FINISHED_JOB_STATES or (lane := session_lane(drive, job["session"])) == holding_job_lane(drive, job):
        return ""
    if lane is None:
        return " of a session the drive no longer binds, so drive job resume hands it to a fresh agent"
    return f" of {lane}'s session, whose SendMessage alone reaches it, so drive job resume posts a text there"


def question_line(question: dict) -> str:
    card = f"refused, so no card draws: {cut(question['refused'].removeprefix('refused: '), 120)}" if question["refused"] and not question["closed"] else question["card_state"]
    waits = f" ({question['waits']})" if question["card"] and question["waits"] else ""
    kept = f", kept in {question['file']}" if question["file"] else ""
    replaced = f", asked as part of the question kept in {question['replaced']}" if question["replaced"] else ""
    return f"asks him: {cut(question['title'] or 'his question card', 80)}; {card}{waits}{kept}{replaced}"


def job_pr_keys(job: dict, registered: Iterable[str]) -> list[str]:
    return list(dict.fromkeys([*map(pr_key, job["prs"]), *registered]))


def job_pr_lines(drive: dict, job_id: str, job: dict) -> list[str]:
    return [pr_line(drive, key, drive["prs"][key]["card_state"]) if key in drive["prs"] else pr_url(key) for key in job_pr_keys(job, job_prs(drive, job_id))]


FEED_SUBSCRIPTION = "cc-slack subscribe --kind alerts,releases,posts --channel <id>"


def held_channels(drive: dict, role: str, project: str | None = None) -> list[str]:
    """The channels a session holds: a seat's communicator its seat's channel alone, the drive's communicator his DM and each channel of the drive no seat's communicator holds, by his words "every project has a communicator to pair with its coordinator and messages in that channel should go to it"."""
    made = drive["slack"]["channels"]
    if role == "communicator" and project:
        return [f"#{made[project]['name']} {made[project]['id']}"] if project in made else []
    if role == "communicator":
        return [f"his DM {slack.dm()}", *(f"#{channel['name']} {channel['id']}" for seat, channel in made.items() if seat not in drive["communicators"])]
    return drive["slack"].get("watch", []) if role == "coordinator" else []


def hold_line(drive: dict, role: str, project: str | None = None) -> str:
    if project and role != "communicator":
        return "Hold no Slack channel: the top coordinator holds the drive's."
    channels = held_channels(drive, role, project)
    if not channels:
        return "Hold no Slack channel."
    if role != "communicator":
        return f"Hold with {FEED_SUBSCRIPTION}: {', '.join(channels)}"
    released = "; slack_unwatch any channel watch of yours this line no longer names, since a seat's communicator holds it now" if not project and drive["communicators"] else ""
    return f"Hold with slack_watch: {', '.join(channels)}{released}"


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


def order_block(drive: dict, order_id: str, order: dict) -> list[str]:
    lines = [f"- {order_line(drive, order_id, order, WHOLE, promise_thread(drive, order))}", f"  parts: {'; '.join(part_line(drive, order_id, part) for part in order['parts'])}"]
    lines.extend(orphans(drive, order_id, WHOLE))
    for job_id in order["jobs"]:
        job = drive["jobs"][job_id]
        lines.append(f"  - {job_line(drive, job_id, job, WHOLE)}")
        if job["said"]:
            lines.append(f"    said: {cut(job['said'], WHOLE)}")
        lines.extend(f"    - {question_line(question)}" for question in job["questions"])
        lines.extend(f"    - {line}" for line in job_pr_lines(drive, job_id, job))
    return lines


def orphans(drive: dict, order_id: str, limit: int) -> list[str]:
    """Each part of the order whose ORPHAN line the watch said, with the gap it said, while it stands: until the part heals, a FREE line hands it to its job or a job on the part moves, by orphan_stands."""
    return [
        f'  ORPHAN "{cut(part, limit)}", said {when(held["orphan_said"])}: {cut(held["orphan_gap"], limit)}'
        for part, held in drive["orders"][order_id]["parts"].items()
        if held["orphan_gap"] not in (None, FREED) and orphan_stands(drive, order_id, part)
    ]


def part_line(drive: dict, order_id: str, part: str) -> str:
    """A part as drive status lists it: a standing part by standing_line, delivered, else the build the watch follows for it, else each job state.carriers says can carry it, with each PR the drive does not track that carries a stopped builder's part, else that no builder carries work he ordered."""
    kept = drive["orders"][order_id]["parts"][part]
    if kept["standing"]:
        return standing_line(drive, order_id, part)
    if delivered(drive, order_id, part):
        return f'"{part}": delivered'
    if following(kept):
        return f'"{part}": the watch follows {kept["follow"]["build"]}, and its end line delivers it'
    held = ", ".join(f"{job['kind']} {job_id} {job['state']}{untracked_line(drive, job)}" for job_id, job in carriers(drive, order_id, part))
    return f'"{part}": {held or ("no builder carries it" if work(drive, order_id, part) else "no job carries it")}'


def standing_line(drive: dict, order_id: str, part: str) -> str:
    """A standing part as drive status lists it: its goal, then for a part kept on a check its command and its last run's exit code and closing line, else its live pass, else when the watch starts the next, and its last pass with the line it ended on."""
    held = drive["orders"][order_id]["parts"][part]
    if check := held["check"]:
        ran = "never run yet" if check["ran"] is None else f"running since {when(check['ran'])}" if check["exit"] is None else f'last run {when(check["ran"])} exited {check["exit"]}: "{cut(check["said"], 80)}"'
        return f'"{part}": standing on "{cut(held["standing"], 80)}", checked by `{cut(check["command"], 80)}`, {ran}'
    live, last = standing_passes(drive, order_id, part)
    due = pass_due(passes(part_jobs(drive, order_id, part)))
    starts = f"at {when(due.isoformat())}" if due and due > now() else "on its next read"
    at_work = f"its live pass {live[1]['kind']} {live[0]} {live[1]['state']}" if live else f"no pass at work, the watch starts the next {starts}"
    ended = f', its last pass {last[1]["kind"]} {last[0]} {last[1]["state"]}: "{cut(last[1]["said"] or "no line", 80)}"' if last else ", no pass ended yet"
    return f'"{part}": standing on "{cut(drive["orders"][order_id]["parts"][part]["standing"], 80)}", {at_work}{ended}'


def standings(drive: dict, order_id: str, order: dict) -> list[str]:
    """Each standing part of the order, listed under its line on the drive page."""
    return [f"  STANDING {standing_line(drive, order_id, part)}" for part, held in order["parts"].items() if held["standing"]]


def untracked_line(drive: dict, job: dict) -> str:
    keys = untracked_carriers(drive, job)
    return f" with {' and '.join(map(pr_url, keys))} carrying it" if keys else ""


def opening(drive: dict, role: str, project: str | None) -> list[str]:
    """Who the session is: its role, or the project it is the sub-coordinator for with its cci lane, and who holds the other sessions."""
    if project and role == "communicator":
        return communicator_opening(drive, project)
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
    seated = [order_id for order_id, order in drive["orders"].items() if order["priority"] == project]
    held = (
        f"{project} of drive {drive['name']}: you run his order {seated[0]} alone, which drive order set --priority put first in line, its jobs,"
        if seated
        else f"project {project} of drive {drive['name']}: you run his orders filed under {project}, their jobs,"
    )
    return [
        (
            f"You are the sub-coordinator for {held} events, landings and installs, while the top coordinator starts, approves and rolls"
            f" back production cuts. The top coordinator: {who(drive, 'coordinator')}. The communicator:"
            f" {who(drive, 'communicator')}. A background agent this session started ignores this block."
        ),
        (
            f"Your cci lane is {lane}: post with --lane {lane}; the watch for {project} and the communicator's records reach"
            f" you at {lane}@{drive['name']}."
        ),
        hold_line(drive, role, project),
    ]


def communicator_opening(drive: dict, project: str) -> list[str]:
    """Who a seat's communicator is: the communicator for one project or priority seat, holding its channel alone, paired with that seat's sub-coordinator, which its owner records reach, while the drive's communicator answers his DM and the drive's own channel."""
    lane = communicator_lane(project)
    paired = drive["subcoordinators"].get(project)
    coordinator = f"the sub-coordinator for {project}, session {paired['session']}, lane {project_lane(project)}" if paired else f"the top coordinator, {who(drive, 'coordinator')}, until a sub-coordinator for {project} binds"
    return [
        (
            f"You are the communicator for {project} of drive {drive['name']}: his messages in {project}'s channel reach you, and you"
            f" answer each there yourself by the communicator rules below. His work there goes to {coordinator}, as his owner record"
            f" on the record tool, which sends it there by default. The drive's communicator, {who(drive, 'communicator')}, answers"
            " his DM and the drive's own channel. A background agent this session started ignores this block."
        ),
        f"Your cci lane is {lane}: the record tool posts as it, and {project}'s sub-coordinator's records reach you at {lane}@{drive['name']}.",
        hold_line(drive, "communicator", project),
    ]


def context(drive: dict, role: str, project: str | None = None) -> str:
    """The SessionStart block: a session's role, its rules, one line per item that needs it now, and the reads; a sub-coordinator's names its project, its cci lane and its subscription."""
    lines = [
        *opening(drive, role, project),
        f"cci: run cci drive use {drive['name']}, and no cci subscribe: your cci alias {alias(drive['name'], role, project)} owns your subscription.",
        *rules(drive, role),
        *([f"Your brief: {brief_path(drive['name'], project)}"] if role == "communicator" else []),
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


def rules(drive: dict, role: str) -> list[str]:
    """The role's rules as its block draws them, numbered under their heading, with the filing line that closes them, then the rules of the repository the drive works in."""
    return [
        f"{title(role).capitalize()} rules:",
        *(f"{number}. {rule}" for number, rule in enumerate(RULES[role], 1)),
        FILED,
        *repo_block(drive),
    ]


def repo_block(drive: dict) -> list[str]:
    """The rules of the repository the drive works in under their heading, as every role session, every job brief, each handler's prompt and each watchdog's brief carry them; none when it sets none."""
    repo = repo_rules(drive)
    return [REPO_RULES_HEADING, *(f"- {rule}" for rule in repo)] if repo else []


def rule_units(drive: dict, role: str, project: str | None = None) -> list[str]:
    """Each line of a session's block that a check before each call shows again once it changes: its hold line, then each of its rules."""
    return [hold_line(drive, role, project), *rules(drive, role)]


def rule_digest(line: str) -> str:
    """What a seat keeps of one line of its rules, read without its number, so a rule added above it leaves it held."""
    return hashlib.sha256(RULE_NUMBER.sub("", line, count=1).encode()).hexdigest()[:16]


def rules_digests(drive: dict, role: str, project: str | None = None) -> list[str]:
    """What a seat keeps of the lines its session holds, one digest each, so a check before each call shows only the lines an install or a change to the repository's rules changed, so the whole block goes in once a session and after each compaction and an install adds only what it changed."""
    return [rule_digest(line) for line in rule_units(drive, role, project)]


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


def his_prs(rows: list[dict]) -> str:
    """drive status --prs: view.his_prs's rows as a markdown table, which say draws as Slack's data table."""
    if not rows:
        return "No open PR carries his Approve or waits on him."
    lines = ["| PR | Stage | What holds it | Owner | Since |", "|---|---|---|---|---|"]
    for row in rows:
        cells = [f"[{row['key']}]({row['url']}) {row['title']}", row["stage"], row["holds"], row["owner"], when(row["since"]) if row["since"] else "now"]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines)


def status_data(drive: dict, seen: dict[str, str], named: bool = False, runs: Callable[[dict], bool] | None = None) -> dict:
    data = {
        "name": drive["name"],
        "roles": drive["roles"],
        "subcoordinators": {project: held | {"lane": project_lane(project)} for project, held in drive["subcoordinators"].items()},
        "orca": seen,
        "slack": {"channel": slack.dm(), "watch": drive["slack"].get("watch", [])},
        "owner": drive["owner"],
    }
    if not named:
        return data | {"orders": {order_id: order_line(drive, order_id, order, 80, promise_thread(drive, order)) for order_id, order in open_orders(drive).items() if runs is None or runs(order)}}
    job_ids = [job_id for order in drive["orders"].values() for job_id in order["jobs"]]
    return data | {
        "orders": drive["orders"],
        "jobs": {job_id: drive["jobs"][job_id] for job_id in job_ids},
        "prs": {key: pr for key, pr in drive["prs"].items() if pr["order"] in drive["orders"]},
    }


def status_text(drive: dict, seen: dict[str, str], shown: dict | None = None, runs: Callable[[dict], bool] | None = None) -> str:
    """The drive page, one line per open order, a coordinator's, whose runs says which orders it runs, only those; with shown, the drive narrowed to the orders ids name, only those, each whole, open or closed, under one summary line, who carries each part read on every order and job of the drive."""
    if shown is not None:
        carrying = drive | {"prs": shown["prs"]}
        lines = [f"{drive['name']}: {len(open_orders(shown))} open of {len(shown['orders'])} orders"]
        lines.extend(line for order_id, order in shown["orders"].items() for line in order_block(carrying, order_id, order))
        return "\n".join(lines)
    lines = [f"{drive['name']}: {len(open_orders(drive))} open of {len(drive['orders'])} orders"]
    lines.extend(role_line(drive, role, seen) for role in ROLES)
    lines.extend(subcoordinator_line(project, held, seen) for project, held in drive["subcoordinators"].items())
    if reading := owner_reading(drive["owner"]):
        lines.append(f"He is {reading}")
    orders = {order_id: order for order_id, order in open_orders(drive).items() if runs is None or runs(order)}
    if incidents := [incident_line(order_id, order) for order_id, order in orders.items() if order["incident"]]:
        lines.extend(["Open incident orders; an alert on the same failing service joins one with drive order add --joins <o>:", *incidents])
    lines.append(
        f"Open orders{'' if runs is None else ' you run'}: {len(orders)}, a line each and one under it for each standing part and each part an ORPHAN named until it heals or a job on it moves;"
        " drive status <id> prints one whole, with its jobs, PRs and questions:"
    )
    lines.extend(line for order_id, order in orders.items() for line in [f"- {order_line(drive, order_id, order, 80, promise_thread(drive, order))}", *standings(drive, order_id, order), *orphans(drive, order_id, 80)])
    repros = sorted(
        (
            (job["updated"], job_id, job)
            for job_id, job in drive["jobs"].items()
            if job.get("repro") and (runs is None or runs(drive["orders"][job["order"]]))
        ),
        reverse=True,
    )[:REPROS_SHOWN]
    if repros:
        lines.append("Reproduction files, newest first:")
        lines.extend(f"- {job_id} ({job['order']}): {job['repro']}" for _, job_id, job in repros)
    return "\n".join(lines)
