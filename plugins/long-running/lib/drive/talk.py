import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, brief, cli, dashboard, limits, picture, reads, refs, render, slack, state, view
from drive.state import DriveError

CATCH_UP_TITLE = re.compile(r"\A\s*\**\s*catch-up since\b[^\n]*\n*", re.IGNORECASE)
SPAN = re.compile(r"\bfor (?:about |~)?(?:(\d+(?:\.\d+)?)(?: ?(m|mins?|minutes?|h|hrs?|hours?))?|(an|half an) hour)\b", re.IGNORECASE)
SPAN_SLACK = timedelta(minutes=5)
CARDS_A_MESSAGE = 5
CARD_KINDS = (("question", "question"), ("tap", "PR card"))
TICKET_FLAGS = {"ticket_id": "--ticket", "ticket_tree": "--ticket-tree", "ticket_agent": "--ticket-agent", "ticket_call": "--ticket-call", "ticket_fingerprint": "--ticket-fingerprint"}
EDIT_TAKES = {"edit", "text", "asked", "claims", "dry_run", *TICKET_FLAGS}
RECORD_WAIT_SECONDS = 1.0
RECORD_POLL_SECONDS = 0.05
ASKING_AGAIN = "Asking again, since it fixes"
BARE_TS = re.compile(r"\d{10}\.\d{6}")


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack, or in a thread outside it named by channel_id, such as an alert's, or with no thread at the root of the channel channel_id names, such as a staging claim to its members, or in the group DM of a person, him and the bot when channel_id is that person's user id, or reword one it made in any of those, and keep the same item on the drive's dashboard, in the words it was given, a catch-up's title, the count of his open cards it closes with and the cards asked since he was last heard its only additions; a post is recorded on the one order cli.post_order decides, never one its caller names, wherever it goes, and a post in a thread is the last line of each open order whose lines go in that thread, whatever order it is recorded on or none, so the watch's clock and each session's overdue line read every line there, and posts nothing from a caller but the communicator and its handlers, a handler known from its first step by the LONG_RUNNING_HANDLER its launch set, delivering the parts its report record names, making the clock time its words promise his next line by the order's due, and adding its thread to the order's threads; a post at another channel's root, or a line in its thread, whose report names an end as until keeps that end on its order, from which the watch owes that thread its closing line; a claim at another channel's root goes out only on a report naming an end still to come, which its words name, and a report naming none or a past one goes back to its writer. A question to a teammate naming the job it is for keeps that job waiting on their reply. A report's post names each correction cci holds re it, which the post may lack, and a correction of a report held for his catch-up, or of one a post carried, its own or his catch-up, posts nothing, naming where that report's copy is. A handler's result line is kept on its entry, for its done record to carry whole. What the drive's checks find in its words, the say hook hands Jev to judge against his words once this has posted them, before only a post a count or a mark refuses, such as one over its length or to him while he is away, a post saying a release may start while a freeze or his release hold it does not name stands, and a dry run. A report's line to him that `away` reads as waiting for his catch-up posts nothing, from every caller, by `held_away`."""
    known = caller(name)
    if (close := post.get("close")) and (post.get("record") or post.get("edit")):
        raise DriveError("nothing posted: close answers the order of what your handler holds and writes its report itself, so pass no record or edit")
    report = cli.report_attrs(post["record"]) if post.get("record") else {}
    order = cli.post_order(known, os.environ.get("CLAUDE_CODE_SESSION_ID"), post, report)
    if close and not order:
        raise DriveError(
            "nothing posted: close finishes only the order the record your handler holds names or the order his message it holds started, and"
            " neither names one, while another order in that thread is not this message's to close: on an order the communicator holds, post your"
            " own report on the cci post tool with kind report, lane communicator, attrs {close: <o>} and no to, and pass its seq as record; on any"
            ' other, send the owner record with his words and order: "<o>", so the session holding it closes it'
        )
    if close:
        held = known["handlers"][state.handler_id(known, os.environ.get("CLAUDE_CODE_SESSION_ID"))]["holds"] or {}
        post = post | {"asked": [*slack.messages(post.get("asked") or ""), *filter(None, [held.get("link")])]}
    elif order and cli.answers_order(known, order, post):
        close, post = True, {key: value for key, value in post.items() if key != "record"}
    if close:
        report = {"close": order, "writer": "communicator", "written": state.stamp()}
    seq = report.get("seq")
    if refused := held_refusal(known, seq, report) or held_away(name, known, post, report, order) or (order and cli.report_refusal(known, order, report)) or (close and cli.unfiled_refusal(known, order)) or unplaced(known, post, report):
        raise DriveError(f"nothing posted: {refused}")
    if refused := unclaimed(known, post, report):
        raise DriveError(f"nothing posted: {refused}; {returned(name, post, report, refused)}")
    if refused := unended(known, post, report):
        raise DriveError(f"nothing posted: {refused}")
    if refused := brief.claim_refusal(post.get("text") or "", name, known):
        raise DriveError(f"nothing posted: {refused}")
    if report.get("catch_up"):
        if known["catch_up"] is None:
            raise DriveError(f"nothing posted: report {seq} is a catch-up, and the drive owes him none, since its last catch-up already posted")
        post = post | {"text": titled(known, post.get("text") or "") + waiting(name, known, post.get("text") or "")}
    if post.get("dry_run"):
        return dry(name, post) + (f"; {cli.delivering(name, order, report)}" if order else "")
    promise = promising(known, post["edit"]) if post.get("edit") else None
    held = list(known["held"]) if report.get("catch_up") else []
    amended = agents.amendments(name, [seq, *held] if seq else [])
    if close:
        report["seq"] = seq = close_report(name, order, post.get("text") or "")
    claimed = cli.claim_close(name, report)
    if key := request_key(name, known, post, report):
        post = post | {"request_key": key}
    try:
        reply = posted(name, post, order, {"record": seq, "report": report if close else None} if order else None)
    except (DriveError, slack.SlackError):
        cli.unclaim_close(name, claimed)
        raise
    link = reply["permalink"]
    line = f"{'EDITED' if post.get('edit') else 'POSTED'} {link}"
    if card := reply.get("card_permalink"):
        line += f" CARD {card}"
    if job_id := post.get("job"):
        line += "; " + awaiting(name, job_id, link)
    handler = state.handler_id(known, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    place = None if post.get("edit") else state.link_thread(link)
    if handler or seq or place:
        with state.edit(name) as data:
            if handler:
                data["handlers"][handler]["posted"].append(line)
            if seq:
                data["carried"][seq] = link
            if place:
                at = state.stamp(slack.sent_at(slack.posted(link, "")))
                for each in state.open_orders(data).values():
                    if state.promise_thread(each) == place:
                        each["last_line"] = max(each["last_line"] or at, at, key=state.parse_time)
    if warning := reply.get("warning"):
        line += f" WARNING {warning}"
    if found := amended["correction"].get(seq):
        line += f"; cci holds corrections of report {seq}, {', '.join(found)}: unless this post carries each, reword it with say(edit={link}, text=…)"
    if report.get("catch_up"):
        line += released(name, link, amended)
    if order:
        line += "; " + record_line(order, *reply["recorder"], close)
    if close:
        line += closed(name, order, report["seq"], link, post.get("text") or "")
    if promise:
        line += repromised(name, promise, reply["sent"])
    return line


def promising(data: dict, link: str) -> dict | None:
    """The open order owing him a line by a time the post an edit at link rewords started, read before the edit goes out, since Slack then holds only its new words: the order whose promised time is that post's own, found with no Slack call, and whether the post's words promise a time, read from Slack only once such an order is found, since a post that kept one time and started the next the drive held waiting promises it with no time in its words."""
    if not (message := state.slack_message(link)):
        return None
    at = slack.sent_at({"ts": message[1]})
    owing = (order_id for order_id, order in data["orders"].items() if order["state"] not in state.CLOSED_ORDER_STATES and state.owed(order) and state.parse_time(order["promised"]) == at)
    if not (order_id := next(owing, None)):
        return None
    return {"order": order_id, "at": at.isoformat(), "timed": cli.promised_due(slack.message(link).get("text") or "", at) is not None}


def repromised(name: str, promise: dict, text: str) -> str:
    """Keep the time an order owes him in step with the edited post that promised it, under the drive's lock: the time its new words promise, owed from the post as its old words' time was, or, when its old words promised a time and its new words name none, no time owed, the drive's own next time that waited on it promised in its place; nothing once a later post kept the time or replaced it while the edit went out. The part of the EDITED line saying so."""
    at = state.parse_time(promise["at"])
    due = cli.promised_due(text, at)
    if not due and not promise["timed"]:
        return ""
    with state.edit(name) as data:
        order = data["orders"][promise["order"]]
        if not state.owed(order) or state.parse_time(order["promised"]) != at:
            return ""
        if due:
            state.remind(order, state.stamp(due), at)
            return f"; its new words promise him {promise['order']}'s next line by {render.when(order['due'])}, now owed"
        dropped, waiting = order["due"], order["next_due"]
        order.update(due=None, promised=None, kept=None, due_said=None)
        if waiting:
            state.promise(order, waiting, state.parse_time(order["next_set"]))
    restarted = f", and the drive's own time {render.when(waiting)} is owed from its next line" if waiting else ""
    return f"; its new words promise no time, so {promise['order']} no longer owes him a line by {render.when(dropped)}{restarted}"


def caller(name: str) -> dict:
    """The drive once say's caller reads as the communicator or its handler; any other caller posts nothing."""
    known = state.load(name)
    if refused := cli.recording_refusal(known, f"only the communicator's handlers post to him: send these words as a report record to communicator@{name}, whose handler posts and records them"):
        raise DriveError(f"nothing posted: {refused}")
    return known


def request_key(name: str, known: dict, post: dict, report: dict) -> str | None:
    """The key cc-slack stamps on each message of the post and reads Slack for before it posts, so a say retried after cc-slack posted and then failed, as dm-status does when its deadline passes while the daemon still posts, gets back what Slack holds instead of posting it again, while a post in other words goes out: a record's post keys on the record, when cci wrote it and the post's words, a handler's post of no record on his message the handler holds and the post's words; an edit or any other post has none, and cc-slack keys it per call."""
    if post.get("edit"):
        return None
    words = hashlib.sha256(json.dumps([post.get(field) for field in ("text", "card", "tap_prs", "visuals")]).encode()).hexdigest()[:16]
    if post.get("record"):
        return f"{int(state.parse_time(report['written']).timestamp() * 1000)}-{name}.{post['record']}.{words}"
    handler = state.handler_id(known, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if not (message := handler and (known["handlers"][handler]["holds"] or {}).get("message")):
        return None
    return f"{int(float(message) * 1000)}-{name}.{message}.{words}"


def awaiting(name: str, job_id: str, link: str) -> str:
    """Keep the job waiting on the reply to the question a post at link asked a teammate for it, which the watch reads each minute until the first message after it from anyone but the drive and a bot frees the job."""
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        job["waits"] = list(dict.fromkeys([*job["waits"], f"reply:{link}"]))
    return f"{job_id} waits on the reply to it"


def recording(name: str, receipt: dict) -> tuple[Path, int]:
    """Keep the receipt of a post on its order in the drive and record the post there in its own process, which outlives the say call and waits out the drive lock, so a say killed, or held behind the lock, after cc-slack returned its post still records it: the receipt, where that process writes the line it ends with, and its pid. say refused, before it posted, a post its session may not record on the order, so the process, which runs as no session, records every post it is handed."""
    kept = state.drive_dir(name) / "posts" / f"{uuid.uuid4().hex}.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(receipt))
    return kept, state.detach(name, ["say-record", str(kept)], "posts.log")


def record_line(order: str, kept: Path, recorder: int, close: bool) -> str:
    """The line the record of a say post on order ends with, read from its receipt: once it lands within RECORD_WAIT_SECONDS, so a slow Slack read, Jev call or drive lock never holds the say call past that, else where the receipt keeps it; a post closing its order waits for its recorder to end, so the order takes the post before it is set done."""
    ends = time.monotonic() + RECORD_WAIT_SECONDS
    while not (line := json.loads(kept.read_text()).get("line")):
        if close:
            os.waitpid(recorder, 0)
            return json.loads(kept.read_text())["line"]
        if time.monotonic() >= ends:
            return f"recording on {order} after this call; its line lands in {kept}, and a failure reaches the meta session as a defect"
        time.sleep(RECORD_POLL_SECONDS)
    return line


def unrecorded(name: str, text: str) -> None:
    """Tell the meta session, as a defect, that a post say made was not recorded on its order."""
    from drive import tools

    tools.sent(name, "communicator", [{"kind": "defect", "to": [f"meta@{name}"], "text": text}])


def record_kept(name: str, kept: Path) -> str:
    """Record a say post on its order from the receipt `recording` kept, waiting out the drive lock however long it is held, since the post is already out, and writing the line it ends with back into the receipt; one that cannot be recorded is sent to the meta session as a defect naming the receipt."""
    receipt = json.loads(kept.read_text())
    with state.waiting_out_lock():
        line = recorded(name, receipt, f"; receipt {kept}")
    tmp = kept.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt | {"line": line}))
    os.replace(tmp, kept)
    return line


def recorded(name: str, receipt: dict, where: str) -> str:
    """Record a say post on its order on its report, the close report the receipt keeps, else read from cci by the receipt's record; one that cannot be recorded is sent to the meta session as a defect, `where` naming the receipt."""
    try:
        report = receipt["report"] if receipt["report"] is not None else cli.report_attrs(receipt["record"]) if receipt["record"] else {}
        return cli.record_post(name, receipt["order"], receipt["link"], report, receipt["sent"], receipt["channel"])[0]
    except (DriveError, slack.SlackError) as error:
        return unkept(name, receipt, error, where)


def unkept(name: str, receipt: dict, error: Exception, where: str) -> str:
    """The line say ends with for a post its order did not take, sent to the meta session as a defect too, `where` naming the receipt when one was kept."""
    line = f"not recorded on {receipt['order']}: {error}"
    unrecorded(name, f"say posted {receipt['link']} but it was {line}{where}")
    return line


def close_report(name: str, order_id: str, text: str) -> str:
    """Write the report closing the order a post answers, its words the post's, from the communicator's lane, before the post goes out, so say claims the order's close for it; its seq."""
    argv = ["cci", "post", "--drive", name, "--lane", "communicator", "--kind", "report", "--attr", f"close={order_id}", "--json", "--text", text]
    try:
        return str(json.loads(reads.run(argv, Path.cwd(), reads.posting()))["seq"])
    except reads.ReadError as error:
        raise DriveError(f"nothing posted: {order_id}'s closing report could not be written: {error}") from None


def closed(name: str, order_id: str, seq: str, link: str, text: str) -> str:
    """Close the order a post answered on its report seq, and keep the post as the return of the reader job the posting handler holds on it, the line that handler ends on; a step that fails is named, since the post is already out."""
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            cli.order_set(cli.parser().parse_args(["order", "set", order_id, "--state", "done", "--record", seq, "--drive", name]))
        except DriveError as error:
            return f"; {order_id} not closed on report {seq}: {error}"
    line = f"; {order_id} closed on report {seq}"
    data = state.load(name)
    if (job_id := state.agent_job(data, state.handler_id(data, os.environ.get("CLAUDE_CODE_SESSION_ID")))) and data["jobs"][job_id]["order"] == order_id:
        result = f"POSTED {link}"
        state.keep_report(name, job_id, result, f"{result}\n{text}")
        with state.edit(name) as data:
            state.take_result(data, job_id, result)
        line += f"; {job_id} keeps {result} as its return, the line you end on"
    return line


def away(data: dict, post: dict, report: dict, order_id: str | None, moment: datetime) -> str | None:
    """Why a line to him waits for his catch-up, by state.AWAY_HOLD, the one rule say and the say hook read: he is away, as the owner row's away reads, and the line goes in his DM as no card, PR card, edit, catch-up or answer passing asked, carries no WAITING-ON-HIM, is no incident's or customer outage's line and names no clock time still to come; None for a line that goes out."""
    if not state.owner_away(data, moment) or any(post.get(key) for key in ("edit", "card", "tap_prs", "asked")) or report.get("catch_up") or place(post)[0] != slack.dm():
        return None
    text = post.get("text") or ""
    if state.WAITS_ON_HIM in text or outage(data, post, report, order_id) or time_to_come(text, moment):
        return None
    return f"he is {render.owner_reading(data['owner'])}, and while he is away only cards, incident lines, lines naming a clock time still to come and answers to his messages go out"


def held_away(name: str, data: dict, post: dict, report: dict, order_id: str | None) -> str | None:
    """Why a report's line to him goes nowhere while `away` reads it waiting: a report owing a line promised by a time is owed at his return, which the watch writes again the first pass after he is heard, so it joins no catch-up; any other is marked held on the drive, once, on the order its post goes to, so `drive status` lists it until his catch-up carries it. The say hook refuses it before Jev reads or sizes it, and say itself refuses it from every caller, so a post the hook never checked, such as the communicator's own or a handler's the hook found unbound, is held too."""
    if not (seq := report.get("seq")) or not (gone := away(data, post, report, order_id, state.now())):
        return None
    if due := report.get("due"):
        return (
            f"he is {render.owner_reading(data['owner'])}, and report {seq} owes the line promised him by {render.when(due)}, which is owed at his return:"
            " the watch writes its report again the first pass after he is heard, so it joins no catch-up. Post nothing and return NOTHING owed at his return."
        )
    hold(name, seq, posted_order(data, post, order_id))
    return (
        f"{gone}. Post nothing: this news joins the catch-up the drive posts when he is back. Report {seq} is marked held:"
        " `drive status` lists it until the catch-up posts. Return NOTHING held for his catch-up."
    )


def time_to_come(text: str, at: datetime) -> bool:
    """Whether a line names a clock time still to come, such as a freeze ending at 5:00 PM, by the drive's own reader of typed times, a promise of the next line aside, so it reaches him before that time rather than in his catch-up."""
    promised = [found.span() for found in cli.PROMISED_TIME.finditer(text)]
    return any(limits.typed_moment(text, found, at) > at for found in limits.CLOCK_TIME.finditer(text) if not any(start <= found.start() < end for start, end in promised))


def outage(data: dict, post: dict, report: dict, order_id: str | None) -> bool:
    """Whether a post reports on an outage he hears at once, never as its words read: an incident, as the order it goes to marks it, or a customer team's outage the drive did not cause, as its report's customer_outage mark says."""
    return report.get("customer_outage") == "true" or bool((posted := posted_order(data, post, order_id)) and data["orders"][posted]["incident"])


def posted_order(data: dict, post: dict, order_id: str | None) -> str | None:
    """The order a post goes to: the one say records it on, else the open order whose own thread, in his DM or the channel its channel_id names, or the thread its lines to him go in, it goes in."""
    where = place(post)
    return order_id or next((each for each, order in state.open_orders(data).items() if where[1] and where in (state.own_thread(order), state.promise_thread(order))), None)


def hold(name: str, seq: str, order_id: str | None) -> None:
    """Mark a report whose line waits for his catch-up held on the drive, once, on the order its post goes to, by `held_away`."""
    with state.edit(name) as data:
        data["held"].setdefault(seq, {"order": order_id, "at": state.stamp()})


def released(name: str, post: str, amended: dict[str, dict[str, list[str]]]) -> str:
    """Settle the catch-up the drive owed him, which just posted at post, as the last line each order it covers got, and let go of every report held for it, keeping post as the copy each went out in but for one its writer withdrew, which went out in none and gives back the close of each open order it claimed, and name them, with each correction cci holds re one carried, which the post must carry, and each withdrawn, which it must leave out."""
    withdrawn = amended["withdraw"]
    with state.edit(name) as data:
        held, data["held"] = data["held"], {}
        carried = [seq for seq in held if seq not in withdrawn]
        data["carried"] |= dict.fromkeys(carried, post)
        for order in data["orders"].values():
            if order["closed_on"] in withdrawn and order["state"] not in state.CLOSED_ORDER_STATES:
                order["closed_on"] = None
        for order_id in data["catch_up"]["orders"]:
            data["orders"][order_id]["last_line"] = state.stamp()
        data["catch_up"] = None
    fixes = [fix for seq in carried for fix in amended["correction"].get(seq, [])]
    line = f"; it carries the reports held for it, no longer held: {', '.join(carried)}" if carried else ""
    line += f"; cci holds corrections {', '.join(fixes)} of them: unless this post carries each, reword it with say(edit={post}, text=…)" if fixes else ""
    if taken := [seq for seq in held if seq in withdrawn]:
        line += f"; their writers withdrew reports {', '.join(taken)}, so no post carries them and no order closes on them: unless this post leaves each out, reword it with say(edit={post}, text=…)"
    return line


def held_refusal(data: dict, seq: str | None, report: dict) -> str | None:
    """Why a report's or a correction's post goes nowhere: the report is held for his catch-up, as the away check marks it whatever Jev then reads, or the report a correction corrects is, since the catch-up carries the correction with it, or went out in a post, its own or his catch-up, which the correction rewords."""
    if seq in data["held"]:
        return f"report {seq} is held for his catch-up, which carries it: return NOTHING held for his catch-up"
    corrected = report.get("corrects")
    if corrected in data["held"]:
        return f"record {seq} corrects report {corrected}, held for his catch-up, which carries this correction with it: return NOTHING held for his catch-up"
    if post := data["carried"].get(corrected):
        return f"record {seq} corrects report {corrected}, which went out in {post}: reword that post with say(edit={post}, text=…)"
    return None


def titled(data: dict, text: str) -> str:
    """His catch-up, opening with the title the tool writes from the time he was last heard before the absence it covers, in place of any title the text came with."""
    return f"*Catch-up since {render.when(data['catch_up']['since'])}*\n{CATCH_UP_TITLE.sub('', text, count=1)}"


def waiting(name: str, data: dict, text: str) -> str:
    """The lines his catch-up closes with: a count of every open card of his the status page queues as waiting on him, linking that queue on the dashboard, then each one asked since he was last heard that the text does not already name, in the queue's order, on its own line, with a blank line after each five, where cc-slack starts its next message since Slack draws five a message. A card asked before he went away stays behind the count, by his words on a catch-up that drew every card: "including all tehse cards each time ^^ is not helpful and is very obtrusive"."""
    try:
        found = view.cards(name)
    except slack.SlackError as error:
        raise DriveError(f"nothing posted: his catch-up counts every open card of his, which cc-slack could not list: {error}") from None
    queued = [item for item in view.build(data, found, state.now())["queue"] if "card" in item]
    if not queued:
        return ""
    counts = [f"{count} {word}{'' if count == 1 else 's'}" for kind, word in CARD_KINDS if (count := sum(item["kind"] == kind for item in queued))]
    count = f"\n\n*<{dashboard.link(name)}#/|Your open cards: {', '.join(counts)}>*"
    named = {match[2] for match in refs.REF.finditer(text) if match[1] == "card"}
    since = state.parse_time(data["catch_up"]["since"])
    lines = [f"{{card:{item['card']['waits']}}}" for item in queued if state.parse_time(item["since"]) > since and item["card"]["waits"] not in named]
    if not lines:
        return count
    messages = ["\n".join(lines[start : start + CARDS_A_MESSAGE]) for start in range(0, len(lines), CARDS_A_MESSAGE)]
    return f"{count}, {len(lines)} new since you were last heard:\n" + "\n\n".join(messages)


def dry(name: str, post: dict) -> str:
    """What say would post, its references resolved and its pictures drawn, with nothing posted, refused as the post itself would be; the say hook judges its words as it judges the post's."""
    found = refs.resolved(name, post.get("text") or "")
    check(name, post)
    visuals, words, lifted = shown(name, post)
    data = state.load(name)
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    where = f"edit of {post['edit']}" if post.get("edit") else ", ".join(shown_at(place) for place in places(data, post, stack))
    card = f", card {post['card'].get('title')!r}" if post.get("card") else ""
    prs = f", PR card for {len(post['tap_prs'])} PRs" if post.get("tap_prs") else ""
    pictures = len([visual for visual in visuals if "picture" in visual]) + len(lifted)
    return f"DRY {where}, {pictures} pictures{card}{prs}: {refs.slack(words, found, shown_in(name, post))}"


def check(name: str, post: dict) -> None:
    """Refuse an edit carrying anything but new words for its post, or a fence that draws as a picture, which an edit of a Slack message cannot add, a PR card naming a PR the drive holds none of, which it cannot draw, a post outside his DM naming his DM, carrying about, which names a card his reply in his DM is about, or drawing a card with no thread, a thread outside his DM named with no channel_id, and a PR card leaving out the thread outside his DM of an incident its PRs carry out, which its line goes in, and a job a question is for named on a post in his DM, on a card or on a job finished or unknown."""
    data = state.load(name)
    if channel := post.get("channel_id"):
        if channel == slack.dm():
            raise DriveError(f"nothing posted: channel_id {channel} is his DM; leave channel_id out, and say posts there through cc-slack dm-status")
        if post.get("about"):
            raise DriveError("nothing posted: about names the card a reply in his DM is about, which cc-slack puts in that card's thread there, so a post with channel_id takes none")
        if not post.get("thread_ts") and (post.get("card") or post.get("tap_prs")):
            raise DriveError(f"nothing posted: a card goes up in his DM and its line in a thread outside it, so with channel_id {channel} it takes that thread's thread_ts")
    if refused := misplaced(data, post):
        raise DriveError(refused)
    if post.get("edit") and (extra := sorted(key for key, value in post.items() if value and key not in EDIT_TAKES)):
        raise DriveError(
            f"nothing edited: an edit rewords the text of one post of yours, so it takes edit, text, asked and claims alone, and this one also"
            f" passes {', '.join(extra)}. Redraw a card with drive job card, and post anything new as a new post"
        )
    if post.get("edit") and not post.get("text"):
        raise DriveError("nothing edited: an edit needs text, the post's new words")
    if post.get("edit") and any(picture.drawable(fence[1], fence[2]) for fence in picture.FENCE.finditer(post["text"])):
        raise DriveError("nothing edited: a code or diagram fence draws as a picture, which an edit of a Slack message cannot add; post it as a new post")
    if job_id := post.get("job"):
        if not post.get("channel_id") or post.get("card") or post.get("tap_prs"):
            raise DriveError("nothing posted: job names the job a question to a teammate is for, so it takes channel_id, their user id or a thread outside his DM, and no card or tap_prs")
        if job_id not in data["jobs"] or data["jobs"][job_id]["state"] in state.FINISHED_JOB_STATES:
            held = data["jobs"][job_id]["state"] if job_id in data["jobs"] else "no job of this drive"
            raise DriveError(f"nothing posted: job {job_id} is {held}, so nothing would wait on the reply")
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    if strays := [key for key in stack if key not in data["prs"]]:
        raise DriveError(f"tap_prs names {', '.join(strays)}, which the drive holds no PR for, so nothing was posted: drive pr add records it")
    outside = [where for where in state.pr_threads(data, stack) if where[0] not in slack.operators()]
    if outside and (post.get("channel_id"), post.get("thread_ts")) not in outside:
        raise DriveError(
            f"nothing posted: {', '.join(stack)} carry out an order whose thread is {outside[0][1]} of channel {outside[0][0]}, so the card's line"
            f" and link go there as well as in his DM: pass channel_id {outside[0][0]} and thread_ts {outside[0][1]}, the place cc-slack's Slack"
            " permission hook judges this say for"
        )


def misplaced(data: dict, post: dict) -> str | None:
    """Why a post naming a thread with no channel_id goes nowhere: the thread is an order's, its own or one it keeps, in a channel outside his DM, such as an alert's, so in his DM Slack finds no such thread."""
    if post.get("channel_id") or post.get("edit") or not (thread := post.get("thread_ts")):
        return None
    place = outside(data, thread)
    return place and f"nothing posted: thread {thread} is in channel {place}, not his DM, so pass channel_id {place} with it"


def unplaced(data: dict, post: dict, report: dict) -> str | None:
    """Why a report's post naming no place goes nowhere: its line goes in the thread its record names and never at his DM root instead, so a line cc-slack's Slack permission hook refuses in its thread goes nowhere else. His catch-up posts at his DM root and a card draws in his DM, so neither is held to it."""
    if not (thread := report.get("thread")) or report.get("catch_up") or any(post.get(key) for key in ("channel_id", "thread_ts", "edit", "about", "card", "tap_prs")):
        return None
    if channel := outside(data, thread):
        return (
            f"report {report['seq']}'s line goes in thread {thread} of channel {channel}, never his DM root: pass channel_id {channel}"
            f" and thread_ts {thread}, the place cc-slack's Slack permission hook judges this say for, and a post it refuses there goes nowhere else"
        )
    return f"report {report['seq']}'s line goes in thread {thread} of his DM, never his DM root: pass thread_ts {thread}"


def unclaimed(data: dict, post: dict, report: dict) -> str | None:
    """Why a post to a channel's members goes back to the writer of its report: the repository's staging-claim rule has each claim say how long it holds, so a post at the root of a channel outside his DM, such as a staging claim, goes out only on a report whose until attr names that end, and a claim or a line in its thread moving it only while that end is still to come; the end is the writer's to set, never the handler's to drop."""
    if post.get("edit") or not to_members(data, post):
        return None
    if until := report.get("until"):
        if state.parse_time(until) > state.now():
            return None
        return f"report {report['seq']}'s until attr ends this claim at {render.when(until)}, which passed before it went out at {render.when(state.stamp())}"
    if place(post)[1]:
        return None
    named = f"report {report['seq']}" if report.get("seq") else "this post"
    return f"{named} names no until attr, the end of what a post at the root of a channel outside his DM, such as a staging claim, holds, which the repository's staging-claim rule has each claim say"


def returned(name: str, post: dict, report: dict, refused: str) -> str:
    """Send a claim's refusal to the session that wrote its report, as a refused record re that report, so that session sets the end again; the clause of say's refusal saying where it went, or would go on a dry run."""
    if not report.get("seq"):
        return "a claim goes out only on a report naming its end, so ask the session it is for to send one with --attr until=<its end in RFC 3339>"
    to = f"{report['writer']}@{name}"
    if post.get("dry_run"):
        return f"say sends this back to {to}, which wrote report {report['seq']}"
    from drive import tools

    made = {"kind": "refused", "re": [report["seq"]], "to": [to], "text": f"report {report['seq']} posted nothing: {refused}; send it again with --attr until=<the end the work now needs>"}
    [seq] = tools.sent(name, "communicator", [limits.shortened(made) or made])
    return f"refused record {seq} sent it back to {to}, which wrote report {report['seq']}: post nothing and return NOTHING sent back to {to}"


def unended(data: dict, post: dict, report: dict) -> str | None:
    """Why a report naming an end goes nowhere: its until attr is the end of a post at the root of a channel outside his DM, such as a staging claim, or of a line in that post's thread moving it, from which the watch owes that thread its closing line, so a post anywhere else would keep no end, and one whose words name neither that end's clock time nor how long it holds, within SPAN_SLACK of it, would tell the channel's members another end than the one the watch holds it to."""
    if not (until := report.get("until")):
        return None
    if not to_members(data, post):
        return (
            f"report {report['seq']}'s until attr is the end of a post at the root of a channel outside his DM, such as a staging claim, or of"
            " a line moving it in that post's thread, and this post goes to neither: pass that channel as channel_id, and that post's ts as thread_ts for a line in its thread"
        )
    end = state.parse_time(until)
    text = post.get("text") or ""
    if any((int(found[1]), int(found[2] or 0), found[3].upper()) == (int(end.strftime("%I")), end.minute, end.strftime("%p")[0]) for found in limits.CLOCK_TIME.finditer(text)):
        return None
    left = end - state.now()
    if any(abs(span(found) - left) <= SPAN_SLACK for found in SPAN.finditer(text)):
        return None
    minutes = round(left / timedelta(minutes=1))
    return (
        f'report {report["seq"]}\'s until attr ends this claim at {render.when(until)}, {minutes} minutes from now, and its words name neither that time nor how long'
        f' it holds: say "until {render.clock(end)}" or "for {minutes} minutes", within {SPAN_SLACK // timedelta(minutes=1)} minutes of it, since the repository\'s'
        " staging-claim rule has each claim say how long it holds; an end you read as too short goes back to"
        f" {report['writer']}, which wrote report {report['seq']}, as a refused record on the cci post tool re {report['seq']}, never left out"
    )


def span(found: re.Match) -> timedelta:
    """How long a claim's words say it holds, as "for 30", "for 2 hours" or "for half an hour" say it, a bare number counting minutes."""
    if found[3]:
        return timedelta(minutes=30 if found[3].lower() == "half an" else 60)
    return timedelta(hours=float(found[1])) if (found[2] or "m")[0].lower() == "h" else timedelta(minutes=float(found[1]))


def outside(data: dict, thread: str) -> str | None:
    """The channel outside his DM a thread of an order is in, its own or one it keeps, such as an alert's; None for a thread of his DM."""
    dm = slack.dm()
    return next((where[0] for order in data["orders"].values() for where in [state.own_thread(order), *order["threads"]] if where and where[1] == thread and where[0] != dm), None)


def shown(name: str, post: dict) -> tuple[list[dict], str, list[str]]:
    """The post's visuals as the dashboard keeps them, each but a chart with the picture Slack gets of it; the text Slack gets, each chart visual added as its ```chart fence, which Slack draws as its own chart, and each fence of the text that draws as a picture lifted out, while the dashboard draws it in place; and the pictures of those lifted fences. A fence of words or of raw data stays in the text, which Slack shows as the code block it is."""
    visuals = [
        {"kind": kind(visual), "source": visual["source"], "caption": visual.get("caption") or ""}
        | ({} if visual["kind"] in picture.NATIVE else {"picture": str(drawn(name, f"visual {index}", visual))})
        for index, visual in enumerate(post.get("visuals") or [], start=1)
    ]
    lifted = []

    def lift(fence: re.Match[str]) -> str:
        if not picture.drawable(fence[1], fence[2]):
            return fence[0]
        language = fence[1].lower()
        visual = {"kind": language} if language in picture.DIAGRAMS else {"kind": "code", "language": language}
        lifted.append(str(drawn(name, f"its ```{fence[1]} fence", visual | {"source": fence[2], "caption": picture.title(kind(visual))})))
        return ""

    text = picture.FENCE.sub(lift, post.get("text") or "")
    charts = ["\n".join(filter(None, [visual["caption"], f"```{visual['kind']}", visual["source"].strip("\n"), "```"])) for visual in visuals if "picture" not in visual]
    return visuals, "\n\n".join(filter(None, [text, *charts])), lifted


def posted(name: str, post: dict, order: str | None = None, receipt: dict | None = None) -> dict:
    """cc-slack's reply to the post, or to the edit rewording a post of its own, which went out with its references linked, each visual drawn as a picture but a chart, which Slack draws itself, and each code or diagram fence of its text drawn as a picture too, and is kept as a said event for the dashboard, naming each message of his it answers for the watch's daily sweep, the dashboard drawing each visual and fence live from its source and each PR of a stack card with its state now, an edit's words in place of its post's; a post that draws a card stores his cards as cc-slack lists them now, so the next post's hook sees that card; a post check refuses goes nowhere, and every cc-slack call making the post carries the post's request key, so a retry gets back each message Slack already holds under it. A post at his DM root goes at the root of its project's operator channel when it has one, the project `project` names. A card's line goes in each thread of his DM or an operator channel its PRs' orders have, and through cc-slack reply in the thread outside his DM the post names, such as an alert's, so whichever he reads carries the card's link, and the thread of each PR card drawn joins every order its PRs serve; the post's permalink is then that line's. A post of words in a thread is kept, by `keep_line`, as the line that thread holds about each card of his it links. A post say records on order keeps receipt, its record and close report, and starts its recorder the moment cc-slack returns it, ahead of every drive write after it, the receipt's path and the recorder's pid then its recorder."""
    text = post.get("text") or ""
    found = refs.resolved(name, text)
    check(name, post)
    visuals, words, lifted = shown(name, post)
    card = post.get("card")
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    data = state.load(name)
    dm = slack.dm()
    sent = refs.slack(words, found, shown_in(name, post))
    pictures = [*(visual["picture"] for visual in visuals if "picture" in visual), *lifted]
    asked = cli.asked_on(name, card["waits"]) if card and card.get("waits") else set()
    drawn = bool(card or stack)
    first, *rest = places(data, post, stack)
    if first == (dm, None) and not post.get("edit"):
        first = (slack.project_channel(name, project(data, post, stack, order)), None)
    operators = slack.operators()
    reply = slack.cc_slack(
        *(
            ["edit", "--url", post["edit"], "--text", sent, *ticketed(post)]
            if post.get("edit")
            else replied(post, pictures, sent)
            if first[0] not in operators
            else status(name, data, post | {"channel_id": first[0], "thread_ts": first[1]}, stack, pictures, sent)
        )
    )
    if receipt is not None:
        reply |= {"recorder": recording(name, receipt | {"order": order, "link": reply["permalink"], "sent": sent, "channel": post.get("channel_id")})}
    if card:
        reply |= {"card_permalink": " ".join([reply["permalink"], *filter(None, [reply.get("card_permalink")])])}
    if card and card.get("waits"):
        cli.follow_card(name, card["waits"], asked, reply["permalink"])
    kept = refs.board(text, found) or (card or {}).get("title") or ""
    state.append_event(
        name,
        "said",
        text=kept,
        channel=None if first[0] == dm else first[0],
        thread=reply.get("thread_ts"),
        url=reply["permalink"],
        card=reply.get("card_permalink"),
        order=order,
        visuals=visuals,
        prs=stack,
        edited=bool(post.get("edit")),
        answers=answered_ts(data, post),
    )
    if drawn:
        view.refresh_cards(name)
    else:
        reply |= {"sent": sent}
        if first[1] and view.drawn_on(data, *view.SLACK_TS.findall(sent)):
            keep_line(name, first[1], reply["permalink"], sent)
    label = card["title"] if card else "; ".join(view.pr_title(data["prs"][key]) for key in stack)
    for place in [place for place in rest if place[0] in operators]:
        linked(name, data, reply, place, stack, refs.slack(words, found, place[1]), label, kept, post.get("request_key"))
    if stack:
        with state.edit(name) as data:
            state.join_pr_cards(data, stack, [reply.get("card_permalink")], state.stamp())
    for place in [place for place in rest if place[0] not in operators]:
        reply |= {"permalink": carried(name, reply, place, post, refs.slack(words, found, place[1]), label, kept, stack)}
    return reply


def answered_ts(data: dict, post: dict) -> list[str]:
    """The ts of each message of his a post answers, by its asked, which the watch's daily sweep times his answers by: a ts or a permalink names its own message, and words name the message the posting handler holds; an edit answers none."""
    asked = [] if post.get("edit") else slack.messages(post.get("asked") or "")
    named = [entry if BARE_TS.fullmatch(entry) else message and message[1] for entry in asked for message in [state.slack_message(entry)]]
    handler = state.handler_id(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    held = handler and (data["handlers"][handler]["holds"] or {}).get("message")
    return list(dict.fromkeys(filter(None, (ts or held for ts in named))))


def keep_line(name: str, thread: str, link: str, sent: str) -> None:
    """Keep a post in a thread as the line that thread holds about each card of his its words link, on each question drawn on that card, by the thread, an edit's words in its post's place: `drive job ask` names it in the report of the thread's next line on the card, by `cli.card_line`, and `drive job set --told` reads the line it names."""
    with state.edit(name) as data:
        for _, question in view.drawn_on(data, *view.SLACK_TS.findall(sent)):
            question["told"][thread] = {"link": link, "words": sent}


def project(data: dict, post: dict, stack: list[str], order: str | None) -> str | None:
    """The project a post at the root is about: its order's, else that of the order of the job its card asks for, else that of the first order its PRs serve that names one."""
    waits = (post.get("card") or {}).get("waits")
    orders = [order] if order else [data["jobs"][job]["order"]] if waits and (job := state.waits_job(data, waits)) in data["jobs"] else [each for key in stack for each in state.served_orders(data, data["prs"][key])]
    return next((data["orders"][each]["project"] for each in orders if each in data["orders"] and data["orders"][each]["project"]), None)


def places(data: dict, post: dict, stack: list[str]) -> list[tuple[str, str | None]]:
    """Each channel and thread the post goes in, (his DM, None) for his DM root, (a channel outside it, None) for that channel's root and (a person's user id, None) for the group DM of that person, him and the bot: the one it names; for a card, which cc-slack dm-status draws in his DM or an operator channel alone, first each place in those, the one it names, then for a PR card each thread of the orders its PRs carry out, else his DM root, then the one outside those it names, such as an alert's thread, which takes the card's line, since cc-slack's Slack permission hook judges a say for the place it names alone."""
    dm, operators = slack.dm(), slack.operators()
    named = place(post)
    if not (post.get("card") or stack):
        return [named]
    linked = [where for where in state.pr_threads(data, stack) if where[0] in operators or where == named]
    spots = list(dict.fromkeys([named, *linked] if named != (dm, None) else linked))
    return [*([where for where in spots if where[0] in operators] or [(dm, None)]), *(where for where in spots if where[0] not in operators)]


def place(post: dict) -> tuple[str, str | None]:
    """The channel and thread a say post is in, None at the channel's root: an edit's are those of the message it rewords, read from its permalink, which an edit naming no Slack message refuses, and a new post's the ones it names, his DM for none."""
    if not post.get("edit"):
        return post.get("channel_id") or slack.dm(), post.get("thread_ts")
    if (message := state.slack_message(post["edit"])) is None:
        raise DriveError(f"nothing edited: edit {post['edit']} is no Slack permalink; pass the link the post's POSTED line gave")
    channel, ts, thread = message
    return channel, None if thread == ts else thread


def to_him(data: dict, channel: str, thread: str | None) -> bool:
    """Whether a say post in this channel and thread, None at its root, speaks to him, which the drive's checks of a line to him and the line an order owes him both read: one in his DM or an operator channel, one in the group DM of a person, him and the bot, its channel that person's user id as say names it, and one in a thread outside his DM, such as an alert's, unless an order keeps that thread as one whose readers are the channel's members: a reporter's, a teammate's he ordered watched, or the thread under a say post at that channel's root, such as a staging claim's; never one at the root of another channel."""
    if channel in slack.operators() or cli.SLACK_USER.fullmatch(channel):
        return True
    members = {(kept[0], kept[1]) for order in data["orders"].values() for kept in order["threads"] if kept[3] == "members"}
    return bool(thread) and (channel, thread) not in members | answered(data)


def to_members(data: dict, post: dict) -> bool:
    """Whether a say post speaks to a channel's members through a post the drive made at that channel's root, such as a staging claim, at that root or in its thread, which the reader's checks read in place of those of a line to him; a line in a reporter's thread or a teammate's he ordered watched answers its people, so it is neither."""
    channel, thread = place(post)
    return not to_him(data, channel, thread) and (not thread or (channel, thread) not in answered(data))


def answered(data: dict) -> set[tuple[str, str]]:
    """Each thread outside his DM where the drive answers the people in it, a reporter's or a teammate's he ordered watched."""
    return {
        where
        for order in data["orders"].values()
        for where in [*((held["channel"], held["thread"]) for held in order["reporters"]), *([(order["watch"]["channel"], order["watch"]["thread"])] if order["watch"] else [])]
    }


def shown_at(place: tuple[str, str | None]) -> str:
    if place[0] == slack.dm():
        return f"thread {place[1]}" if place[1] else "his DM root"
    if place[1]:
        return f"thread {place[1]} of channel {place[0]}"
    return f"the group DM of {place[0]}, him and the bot" if cli.SLACK_USER.fullmatch(place[0]) else f"the root of channel {place[0]}"


def shown_in(name: str, post: dict) -> str | None:
    """The thread a post goes in first, the thread of the card its `about` names, where cc-slack puts that reply, None for his DM root: a card named in its own thread reads there as words."""
    if post.get("about"):
        return view.card_thread(refs.card(refs.drawn_cards(name), state.load(name), post["about"])["url"])
    return places(state.load(name), post, [state.pr_key(each) for each in post.get("tap_prs") or []])[0][1]


def linked(name: str, data: dict, reply: dict, where: tuple[str, str], stack: list[str], sent: str, label: str, kept: str, key: str | None) -> None:
    """Post a PR card's line in one more thread of his DM or of an operator channel, linking the card cc-slack drew under the first line, which keeps its buttons there, since a line naming the PRs in tap_prs moves their card under itself, and keep it for the dashboard. A failure says the card already went out, so nothing is posted again."""
    channel, thread = where
    card, text = card_link(reply, sent, label)
    try:
        line = slack.cc_slack(*status(name, data, {"channel_id": channel, "thread_ts": thread, "request_key": key}, [], [], text))
    except slack.SlackError as error:
        raise slack.SlackError(f"posted {reply['permalink']} with its card, but its line in thread {thread} did not go out, so post neither again: {error}") from None
    state.append_event(name, "said", text=kept, channel=None if channel == slack.dm() else channel, thread=line.get("thread_ts"), url=line["permalink"], card=card, order=None, visuals=[], prs=stack, edited=False)


def card_link(reply: dict, sent: str, label: str) -> tuple[str, str]:
    """The card a say post drew, by the first link its reply names, else the post's own, and the line a post in another thread carries: its words, then that card's link under the label."""
    card = (reply.get("card_permalink") or reply["permalink"]).split()[0]
    return card, "\n".join(filter(None, [sent, f"<{card}|{label.translate(refs.LINK_WORDS)}>"]))


def carried(name: str, reply: dict, place: tuple[str, str], post: dict, sent: str, label: str, kept: str, stack: list[str]) -> str:
    """Post a card's line in a thread outside his DM, such as an alert's, linking the card cc-slack drew in his DM, since cc-slack dm-status links a card into his DM's threads alone, through cc-slack reply with the ticket cc-slack's Slack permission hook attached to the say call, and keep it for the dashboard; its permalink. A failure says the card already went out, so nothing is posted again."""
    card, text = card_link(reply, sent, label)
    try:
        line = slack.cc_slack("reply", "--agent", "claude", "--channel", place[0], "--thread", place[1], "--text", text, *keyed(post), *ticketed(post))
    except slack.SlackError as error:
        raise slack.SlackError(f"posted {card}, but its line in thread {place[1]} of channel {place[0]} did not go out, so post neither again: {error}") from None
    state.append_event(name, "said", text=kept, channel=place[0], thread=line.get("thread_ts"), url=line["permalink"], card=card, order=None, visuals=[], prs=stack, edited=False)
    return line["permalink"]


def status(name: str, data: dict, post: dict, stack: list[str], pictures: list[str], sent: str) -> list[str]:
    """The `cc-slack dm-status` call making a new post in his DM, or in the operator channel channel_id names, as the session holding it from every session but a handler's, so his replies under a post a coordinator, the meta session or the watch made reach the communicator, and a handler's stay with that handler while it lives, each PR its card taps linked from data, the drive read before the post. A post made as the holder answers none of his messages above it in cc-slack, so each stays owed until the handler answering it posts in its thread."""
    argv = ["dm-status", "--agent", "claude", "--drive", name]
    if not state.handler_id(data, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        argv.append("--as-holder")
    if (channel := post.get("channel_id")) and channel != slack.dm():
        argv += ["--channel", channel]
    if thread := post.get("thread_ts"):
        argv += ["--thread", thread]
    if card := post.get("card"):
        argv += ["--card", json.dumps(card)]
    if about := post.get("about"):
        argv += ["--about", about]
    argv += [flag for key in stack for flag in ("--tap-pr", data["prs"][key]["url"])]
    argv += [flag for path in pictures for flag in ("--file", path)]
    argv += keyed(post)
    return [*argv, "--", sent] if sent else argv


def draw(name: str, keys: list[str], again: bool = False) -> str:
    """Draw his card on the PRs, as drive pr add draws it on the row it makes and the watch on a PR when it first needs his tap: cc-slack redraws the card where it sits, offering Approve again when drive card still asks it, or first posts it in the thread of the first order they carry out in his DM or an operator channel, at the root of their project's operator channel when they carry out none, his DM's when that project has none, and posts nothing else; `again`, on PRs that wait on him while they fix a live bug, posts instead the line `waits_line` writes in that thread, or in his open card's own, naming the PRs, and cc-slack posts the card with its buttons right under that line and marks the card it replaces, so the line never goes out with nothing to tap under it. Keep it as a said event and store his cards as cc-slack lists them now, which view.tap_card reads; the card's link. A card drive card refuses for every one of them, a line `plain` refuses, or a card whose waits id names a card he answered while no PR on it changed since, so his answer stands, posts nothing and says why; a line asking again with no thread to go in, since no order of theirs has one and no open card of his shows their heads, posts nothing and says why, and a line cc-slack drew no card under is kept and said, so it is not posted again."""
    from drive import card

    drawn = card.cards([{"repo": key.split("#")[0], "number": int(key.split("#")[1])} for key in keys])
    if not drawn["cards"]:
        raise DriveError("; ".join(f"{skip['repo']}#{skip['number']}: {skip['reason']}" for skip in drawn["skipped"]) or f"drive card drew no card on {', '.join(keys)}")
    found = view.live_cards(name)
    for each in drawn["cards"]:
        waits = each["card"].get("waits")
        closed = waits and not view.card_open(waits, found) and view.closed_card(found, waits)
        if closed and closed["answer"] and not closed["answer"].get("withdrawn") and not closed["replaced_by"] and not any(row["changed"] for row in each["card"]["prs"]):
            raise DriveError(f"nothing was posted: his card under {waits} is {closed['url']}, which he answered, and with no PR on it changed since, his answer stands")
    data = state.load(name)
    titles = [row["title"] for each in drawn["cards"] for row in each["card"]["prs"]]
    line = waits_line(data, keys, titles) if again else "; ".join(titles)
    if again:
        plain(line)
    channel, thread = next((where for where in state.pr_threads(data, keys) if where[0] in slack.operators()), (slack.project_channel(name, project(data, {}, keys, None)), None))
    standing = next(filter(None, (view.tap_card(data, data["prs"][key], found) for key in keys)), None) if again else None
    under = thread or (standing and view.card_thread(standing))
    if again and not under:
        raise DriveError(f"nothing was posted: no order of {', '.join(keys)} has a thread and no open card of his shows their heads, so no thread holds the card to ask his tap again under")
    reply = slack.cc_slack(*status(name, data, {"channel_id": channel, "thread_ts": under}, keys, [], line if again else ""))
    link = next(iter((reply.get("card_permalink") or "").split()), None)
    state.append_event(name, "said", text=line, channel=None if channel == slack.dm() else channel, thread=reply.get("thread_ts"), url=reply["permalink"], card=link, order=None, visuals=[], prs=keys, edited=False)
    view.refresh_cards(name)
    if not link:
        raise DriveError(f"posted {reply['permalink']}, so post it not again, but cc-slack named no card of {', '.join(keys)} under it: {reply.get('warning') or 'it gave no warning'}")
    return link


def waits_line(data: dict, keys: list[str], titles: list[str]) -> str:
    """The line the drive posts when it asks his tap again on PRs that fix a live bug, with the card cc-slack posts under it: why it asks, the open incident or reported bug they fix, and what the PRs on it do, by their titles."""
    incident = any(data["orders"][order_id]["incident"] for key in keys for order_id in state.served_orders(data, data["prs"][key]))
    return f"{ASKING_AGAIN} {'an open incident' if incident else 'a reported bug'}: {'; '.join(titles)}"


def plain(line: str) -> None:
    """Refuse a line asking his tap again that `cc-slack check-post` finds something in and Jev, in one request with one Noul a finding, reads as breaking the plain-words rule at settled.YES or more; a line Jev reads in the band between settled.NO and settled.YES, or gives no reading of, goes out, said on stderr, as a say post does when its judge cannot finish."""
    from drive import settled

    if not (findings := slack.post_findings(line, True)):
        return
    items = (({"findings": {f"f{number}": {"rule": agents.PLAIN_WORDS_RULE, "found": found}}}, {f"f{number}.holds": settled.asking(settled.HOLDS, finding=f"f{number}")}) for number, found in enumerate(findings, 1))
    told, questions, _ = settled.fill({"post": {"text": line}}, {}, items)
    try:
        read = settled.jev(told, questions)
    except settled.Unheard as failure:
        print(f"drive: Jev gave no reading of the line asking his tap again, so it goes out with what check-post found in it, {'; '.join(findings)}: {failure}", file=sys.stderr)
        return
    holds = {found: settled.reading(read.get(f"f{number}.holds")) for number, found in enumerate(findings, 1)}
    if broken := [found for found, held in holds.items() if held]:
        raise DriveError(f"nothing was posted: the line asking his tap again breaks the plain-words rule, as `cc-slack check-post` finds and Jev reads it: {'; '.join(broken)}. Line: {line}")
    if unsure := [found for found, held in holds.items() if held is None]:
        print(f"drive: Jev is unsure whether the plain-words rule holds against {'; '.join(unsure)} in the line asking his tap again, so it goes out", file=sys.stderr)


def withdraw(name: str, key: str) -> list[str]:
    """Withdraw each open card of his carrying a PR reworked to his words, his words its reason, linked to the message he said them in, so no card asks his tap on it until its builder's READY says how they are met and the watch draws a fresh one, and redraw one withdrawn under another reason; each card withdrawn."""
    pr = state.load(name)["prs"][key]
    link = pr["rework"]["link"]
    withdrawn = slack.withdraw_pr_card(pr["url"], state.rework_reason(pr), link if limits.PAGE_LINK.fullmatch(link) else "")
    view.refresh_cards(name)
    return withdrawn


def replied(post: dict, pictures: list[str], sent: str) -> list[str]:
    """The cc-slack call making a new post outside his DM, with the ticket cc-slack's Slack permission hook attached to the say call: `cc-slack reply` in a thread, or with no thread `cc-slack send` at the root of a channel, or to a person's user id, which opens the group DM of that person, him and the bot."""
    thread = ["--thread", post["thread_ts"]] if post.get("thread_ts") else []
    argv = ["reply" if thread else "send", "--agent", "claude", "--channel", post["channel_id"], *thread, "--text", sent]
    argv += [flag for path in pictures for flag in ("--file", path)]
    return argv + keyed(post) + ticketed(post)


def keyed(post: dict) -> list[str]:
    return ["--request-key", post["request_key"]] if post.get("request_key") else []


def ticketed(post: dict) -> list[str]:
    """The flags handing cc-slack the ticket its Slack permission hook attached to the say call, which the daemon spends on a write outside his DM and on an edit."""
    return [each for field, flag in TICKET_FLAGS.items() if post.get(field) for each in (flag, post[field])]


def unquoted_tooling(data: dict, stack: list[str], text: str) -> str | None:
    """Why a PR card may not go out: a PR on it serves a drive tooling order whose own words the line above the card leaves out."""
    for key in stack:
        order = data["orders"].get(state.order_served(data["prs"][key]))
        if order and order["text"].startswith(state.TOOLING) and order["text"] not in text:
            return limits.Refusal(
                f'{key} serves a drive tooling order, so the line above its card quotes that order: put "{order["text"]}" in text',
                f'{key} serves a drive tooling order, and the line above its card leaves out its words "{order["text"]}"',
            )
    return None


def kind(visual: dict) -> str:
    return f"code:{visual.get('language') or ''}" if visual["kind"] == "code" else visual["kind"]


def drawn(name: str, which: str, visual: dict) -> Path:
    """The picture Slack shows for one visual, named after its caption, which Slack titles it with: code highlighted, a diagram or compare drawn as the dashboard draws it, an image as it is; one that does not draw posts nothing."""
    return picture.shot(
        state.drive_dir(name) / "pictures", picture.file_name(visual.get("caption") or "") or "show-me", kind(visual), visual["source"], f"nothing was posted: {which} ({visual['kind']})"
    )
