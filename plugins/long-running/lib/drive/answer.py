import json
import os
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path

from drive import render, slack, state, talk
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
NUMBER = re.compile(r"(?<![\w.])#?\d{3,}\b|\bj\d+\b|\bo\d+\b")
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
    rows += [f"  - {render.question_line(question)}" for question in job["questions"] if not question.get("closed")]
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


def ask(rows: str, words: str) -> tuple[str, dict]:
    """The model's reply to his words over the rows, from one stateless tool-less call, and its usage."""
    prompt = f"<rows>\n{rows}\n</rows>\n\n<his-message>\n{words}\n</his-message>"
    argv = [
        "claude", "-p", "--model", MODEL, "--effort", EFFORT, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence", "--system-prompt", SYSTEM, "--output-format", "json",
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
    """Why an answer is not posted: a link or number the rows do not print, or more than WORDS words."""
    if strays := [url for url, _ in SLACK_LINK.findall(text) if url not in rows] + [url for url in URL.findall(SLACK_LINK.sub("", text)) if url not in rows]:
        return f"it links {strays[0]}, which the rows do not print"
    if strays := [number for number in NUMBER.findall(SLACK_LINK.sub(lambda link: link[2], text)) if number.lstrip("#") not in rows]:
        return f"it names {strays[0]}, which the rows do not print"
    if (count := len(SLACK_LINK.sub(lambda link: link[2], text).split())) > WORDS + 10:
        return f"it runs {count} words, past {WORDS}"
    return None


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


def claim_path(name: str, ts: str) -> Path:
    return state.drive_dir(name) / "answers" / f"{ts}.json"


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
    found = {"answered": None, "why": why, "said": said, "rows_bytes": len(rows), **spent}
    if text and not dry_run:
        reply = talk.posted(name, {"text": text, "thread_ts": thread, "asked": link, "answering": holder_session(data)})
        found["answered"] = reply["permalink"]
    if not dry_run:
        slack.cc_slack("pickup", "--url", link, "--session", holder_session(data), "--done")
    log = state.drive_dir(name) / "answers.log"
    with log.open("a") as out:
        out.write(json.dumps({"at": state.stamp(), "link": link, "dry_run": dry_run, **{key: value for key, value in found.items() if key != "said"}, "said": cut(said, 600)}) + "\n")
    return found | ({"text": text} if text else {})
