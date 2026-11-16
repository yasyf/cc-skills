import json
import os
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path

from drive import reads, render, slack, state, talk
from drive.render import cut

MODEL = "sonnet"
EFFORT = "medium"
WORDS = 60
ROWS_BYTES = 24000
RECENT = timedelta(minutes=90)
ASK_SECONDS = 90
CLAIM_WAIT = ASK_SECONDS + 30
ESCALATE = "ESCALATE"
ANSWER = "ANSWER"
URL = re.compile(r"https?://[^\s|>)\]]+")
SLACK_LINK = re.compile(r"<(https?://[^|>]+)\|([^>]*)>")
NUMBER = re.compile(r"(?<![\w.])#?\d{3,}\b")
DRIVE_ID = re.compile(r"\b[jo]\d+\b|(?<![\w.#/])\d{6,}\b")
JARGON = re.compile(r"\b(?:handoffs?|handlers?|records?|lanes?|seqs?|waits ids?|briefs?|POST|ESCALATE)\b")
LABEL_WORD = re.compile(r"[A-Za-z]{2,}")
SYSTEM = f"""You answer the owner of a software drive in his Slack thread. You get the drive's rows for that thread, printed by a script, then his message.

Reply with exactly one of these two shapes and nothing else:

{ANSWER}
<the answer>

{ESCALATE} <a few words on why>

Answer when the rows hold what he asks: where work stands, what passed or failed, what runs now, what it waits on. Escalate when he gives an order, a ruling, an approval or a correction, asks for new work or a change, or the rows do not hold the answer.

The answer:
- at most {WORDS} words, plain language a busy engineer reads in five seconds, no preamble, never restating his question;
- every fact from the rows, never inferred: a state, number or name the rows do not print is never written;
- link each PR, build or card you mention, in Slack form <url|plain words>, only urls printed in the rows, the words saying what the link shows;
- name a test or check by what it tests, never by a code such as c5 or a status number;
- no job or order ids, no internal words such as handler, record, lane, order or job: say "the staging test of memory", not "j6639";
- lead with the outcome: done, red, running, or waiting on what."""


def thread_orders(data: dict, channel: str, thread: str) -> list[str]:
    """Each order whose lines go in his thread or that came into it, open ones first, the latest moved first."""
    found = [
        order_id
        for order_id, order in data["orders"].items()
        if (order["thread"] and tuple(order["thread"]) == (channel, thread)) or any(ts == thread for _, ts, _, _ in order["threads"])
    ]
    return sorted(found, key=lambda order_id: (data["orders"][order_id]["state"] in state.CLOSED_ORDER_STATES, -state.parse_time(data["orders"][order_id]["updated"]).timestamp()))


def recent(stamp: str | None) -> bool:
    return bool(stamp) and state.now() - state.parse_time(stamp) <= RECENT


def job_rows(data: dict, job_id: str, job: dict, registered: dict[str, list[str]]) -> list[str]:
    last = job.get("report") or job.get("said") or ""
    rows = [f"- job: {render.job_line(data, job_id, job, 160)}; updated {render.when(job['updated'])}" + (f"; its last line: {cut(' '.join(last.split()), 400)}" if last else "")]
    for key in render.job_pr_keys(job, registered.get(job_id, [])):
        rows.append(f"  - PR {state.pr_url(key)}: {cut(render.pr_line(data, key, data['prs'][key].get('card_state')), 300)}" if key in data["prs"] else f"  - PR {state.pr_url(key)}")
    rows += [f"  - open card asking him: {cut(question['title'] or 'his question card', 120)}" + (f" {question['card']}" if question.get("card") else "") for question in job["questions"] if not question.get("closed")]
    return rows


def order_rows(data: dict, order_id: str, registered: dict[str, list[str]]) -> list[str]:
    order = data["orders"][order_id]
    rows = [f"Order {render.order_line(data, order_id, order, 200)}", *([f"  note: {cut(' '.join(order['note'].split()), 500)}"] if order["note"] else [])]
    for job_id in order["jobs"]:
        job = data["jobs"][job_id]
        if job["state"] not in state.FINISHED_JOB_STATES or recent(job["updated"]):
            rows += job_rows(data, job_id, job, registered)
    return rows


def thread_rows(data: dict, channel: str, thread: str) -> str:
    """The drive's rows for his thread as a script prints them: each order in it, its note, each of its jobs unfinished or updated in the last RECENT with that job's last line, PRs and open questions, open orders first, cut to ROWS_BYTES."""
    registered = render.prs_by_job(data)
    rows: list[str] = [f"Now: {render.when(state.stamp())}"]
    for order_id in thread_orders(data, channel, thread):
        order = data["orders"][order_id]
        if order["state"] in state.CLOSED_ORDER_STATES and not recent(order["updated"]):
            continue
        block = order_rows(data, order_id, registered) if order["state"] not in state.CLOSED_ORDER_STATES or not order["text"].startswith(state.TOOLING) else [f"Order {render.order_line(data, order_id, order, 120)}"]
        if sum(len(row) + 1 for row in [*rows, *block]) > ROWS_BYTES:
            break
        rows += block
    return "\n".join(rows)


def ask(rows: str, words: str, system: str = SYSTEM, tag: str = "his-message") -> tuple[str, dict]:
    """The model's reply to the words under tag over the rows, from one stateless tool-less call, and its usage."""
    prompt = f"<rows>\n{rows}\n</rows>\n\n<{tag}>\n{words}\n</{tag}>"
    argv = [
        "claude", "-p", "--model", MODEL, "--effort", EFFORT, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence", "--system-prompt", system, "--output-format", "json",
    ]
    env = {key: value for key, value in os.environ.items() if not key.startswith(("CLAUDE_CODE_SESSION", "CLAUDE_LONG_RUNNING"))} | {"DISABLE_AUTOUPDATER": "1"}
    try:
        done = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=ASK_SECONDS, env=env, cwd=Path.home(), check=False)
    except subprocess.TimeoutExpired:
        return f"{ESCALATE} the model gave no answer in {ASK_SECONDS} seconds", {}
    if done.returncode:
        return f"{ESCALATE} claude -p exited {done.returncode}: {cut(done.stderr.strip(), 200)}", {}
    reply = json.loads(done.stdout)
    return reply["result"].strip(), {"usage": reply.get("usage"), "cost": reply.get("total_cost_usd"), "ms": reply.get("duration_ms")}


def unbacked(text: str, rows: str) -> str | None:
    """Why a post is not posted: a link or number the rows do not print, a link with no words naming it, a drive id, record seq or drive word such as handler or handoff he cannot read, or more than WORDS words."""
    if bare := URL.findall(SLACK_LINK.sub("", text)):
        return f"it links {bare[0]} bare, with no words saying what it shows"
    if unnamed := [url for url, label in SLACK_LINK.findall(text) if len(LABEL_WORD.findall(label)) < 2]:
        return f"its link to {unnamed[0]} has no words saying what it shows"
    words = SLACK_LINK.sub(lambda link: link[2], text)
    if found := DRIVE_ID.search(words):
        return f"it names {found[0]}, a drive id he cannot open"
    if found := JARGON.search(words):
        return f"it says {found[0]}, a word of the drive's own he does not use"
    if strays := [url for url, _ in SLACK_LINK.findall(text) if url not in rows] + [url for url in URL.findall(SLACK_LINK.sub("", text)) if url not in rows]:
        return f"it links {strays[0]}, which the rows do not print"
    if strays := [number for number in NUMBER.findall(SLACK_LINK.sub(lambda link: link[2], text)) if number.lstrip("#") not in rows]:
        return f"it names {strays[0]}, which the rows do not print"
    if (count := len(SLACK_LINK.sub(lambda link: link[2], text).split())) > WORDS + 10:
        return f"it runs {count} words, past {WORDS}"
    return None


def again(words: str, said: str, why: str) -> str:
    """The words for a second call after the check refused the first reply, naming why, so a fixable slip such as a link's missing words costs one more call, never a handler."""
    return f"{words}\n\nYour last reply was:\n{said}\n\nIt was refused: {why}. Reply again in the same shape, fixing that."


def reply_of(said: str, rows: str) -> tuple[str | None, str]:
    """The answer to post and why, or None and why the message goes to a handler instead."""
    head, _, rest = said.partition("\n")
    if head.strip() != ANSWER:
        return None, said.removeprefix(ESCALATE).strip() or "the model escalated it"
    text = rest.strip()
    if refused := unbacked(text, rows):
        return None, refused
    return text, "answered from the drive's rows"


def holder_session(data: dict) -> str:
    from drive import agents

    return agents.beside(data)[1]["session"]


def drop_handed(name: str, link: str) -> None:
    """Take his answered message out of every brief handed to a handler, so one starting later posts no second answer to it."""
    from drive import agents

    ts = state.slack_message(link)[1]
    with state.edit(name) as data:
        for entry in data["handlers"].values():
            entry["handed"] = [brief for brief in entry["handed"] if not (agents.HIS_WORDS in brief and (found := agents.LINK.search(brief)) and (named := state.slack_message(found[1])) and named[1] == ts)]


def claim_path(name: str, ts: str) -> Path:
    return state.drive_dir(name) / "answered" / f"{ts}.json"


def claimed(path: Path) -> dict:
    """What the answer that claimed his message first found, waiting up to CLAIM_WAIT while it runs, so the watch and the communicator's handler call never both answer him."""
    deadline = time.time() + CLAIM_WAIT
    while not (kept := path.read_text()) and time.time() < deadline:
        time.sleep(0.5)
    return json.loads(kept) if kept else {"answered": None, "why": f"the answer that claimed it found nothing in {CLAIM_WAIT} seconds"}


def answer(name: str, link: str, words: str, dry_run: bool = False) -> dict:
    """Answer his message from the drive's rows for its thread within seconds, once whichever of the watch and the handler verb reaches it first, by its claim file: eyes on it at once, one tool-less model call over those rows, and the answer posted in his thread as the session holding his DM, which answers his message there, or, when the model escalates or its answer names what the rows do not print, nothing posted and the reason, for a handler to take it; a later call on the same message returns what the first found."""
    channel, ts, thread = state.slack_message(link)
    if dry_run:
        return found_answer(name, link, words, channel, thread or ts, True)
    path = claim_path(name, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x"):
            pass
    except FileExistsError:
        return claimed(path)
    found = {"answered": None, "why": "its answer raised"}
    try:
        found = found_answer(name, link, words, channel, thread or ts, False)
    finally:
        path.write_text(json.dumps({key: found[key] for key in ("answered", "why")}))
    return found


def found_answer(name: str, link: str, words: str, channel: str, thread: str, dry_run: bool) -> dict:
    if not dry_run:
        slack.cc_slack("pickup", "--url", link, "--session", holder_session(state.saved(name)), "--state", "Reading the drive…")
    data = state.load(name)
    rows = thread_rows(data, channel, thread)
    said, spent = ask(rows, words)
    text, why = reply_of(said, rows)
    if not text and said.startswith(ANSWER):
        said, spent = ask(rows, again(words, said, why))
        text, why = reply_of(said, rows)
    found = {"answered": None, "why": why, "said": said, "rows_bytes": len(rows), **spent}
    if text and not dry_run:
        reply = talk.posted(name, {"text": text, "thread_ts": thread, "asked": link, "answering": holder_session(data)})
        found["answered"] = reply["permalink"]
        drop_handed(name, link)
    if not dry_run:
        slack.cc_slack("pickup", "--url", link, "--session", holder_session(data), "--done")
    log = state.drive_dir(name) / "answers.log"
    with log.open("a") as out:
        out.write(json.dumps({"at": state.stamp(), "link": link, "dry_run": dry_run, **{key: value for key, value in found.items() if key != "said"}, "said": cut(said, 600)}) + "\n")
    return found | ({"text": text} if text else {})


POST = "POST"
NOTHING = "NOTHING"
REPORT_SYSTEM = f"""You write the one line a software drive owes its owner in his Slack thread. You get the drive's rows for that thread, printed by a script, then the report the drive's watch wrote on what moved there.

Reply with exactly one of these three shapes and nothing else:

{POST}
<the line>

{NOTHING} <a few words on why>

{ESCALATE} <a few words on why>

Post when something he would care about moved: a job finished or found something, a PR went up, merged, went red or green, a test passed or failed, work now waits on him or on something new. Say {NOTHING} when nothing he would act on or want to know moved. Escalate when the line needs a decision, a card or a read the rows do not hold.

The line:
- at most {WORDS} words, plain language a busy engineer reads in five seconds, leading with what changed;
- every fact from the rows or the report, never inferred: a state, number or name neither prints is never written;
- link each PR, build or card you mention, in Slack form <url|plain words>, only urls printed there, the words saying what the link shows;
- name a test or check by what it tests, never by a code such as c5;
- no job, order or record ids and no internal words such as handler, record, lane, order, job, handoff or brief;
- never a promise of a time for the next line."""
SCRIPTED_LANES = ("watch",)


def scripted(record: dict) -> bool:
    """Whether a report's line is the drive's own to write from its rows: a progress report the watch wrote, such as a quiet, due or owed one, never one answering another person's words in a watched thread or closing an order."""
    attrs = record["fields"].get("attrs") or {}
    return record["lane"] in SCRIPTED_LANES and record["kind"] == "report" and attrs.get("progress") == "true" and not attrs.get("watch") and not attrs.get("close") and bool(attrs.get("thread"))


def report_of(said: str, rows: str) -> tuple[str | None, str | None, str]:
    """The line to post, or NOTHING, and why; (None, None, why) when the report goes to a handler."""
    head, _, rest = said.partition("\n")
    verb, _, why = head.strip().partition(" ")
    if verb == NOTHING:
        return None, NOTHING, why or "nothing he acts on moved"
    if verb != POST:
        return None, None, head.removeprefix(ESCALATE).strip() or "the model escalated it"
    text = rest.strip()
    if refused := unbacked(text, rows):
        return None, None, refused
    return text, POST, "posted from the drive's rows"


def post_report(name: str, seq: str) -> dict | None:
    """Post the line a watch report owes him from the drive's rows for its thread and the report's own facts, with no handler session: one tool-less model call, the line checked as an answer is, and the report carried by that post, or by NOTHING when nothing he acts on moved; None when the report is not the drive's own to write or the model escalates, for a handler to take it."""
    from drive import agents

    record = agents.shown(seq)
    if not scripted(record):
        return None
    thread = record["fields"]["attrs"]["thread"]
    report = reads.run(["cci", "read", seq], Path.cwd())
    rows = thread_rows(state.load(name), slack.dm(), thread)
    said, spent = ask(rows, report, REPORT_SYSTEM, "report")
    text, verb, why = report_of(said, f"{rows}\n{report}")
    if not verb and said.startswith(POST):
        said, spent = ask(rows, again(report, said, why), REPORT_SYSTEM, "report")
        text, verb, why = report_of(said, f"{rows}\n{report}")
    found = {"seq": seq, "posted": None, "verb": verb, "why": why, **spent}
    if text:
        found["posted"] = talk.posted(name, {"text": text, "thread_ts": thread})["permalink"]
    if verb:
        agents.carry(name, [seq], found["posted"] or f"nothing to post, by the drive's rows: {why}")
    with (state.drive_dir(name) / "answers.log").open("a") as out:
        out.write(json.dumps({"at": state.stamp(), **{key: value for key, value in found.items() if key != "usage"}, "tokens": sum(value for key, value in (spent.get("usage") or {}).items() if key.endswith("tokens") and isinstance(value, int)), "said": cut(said, 600)}) + "\n")
    return found if verb else None
