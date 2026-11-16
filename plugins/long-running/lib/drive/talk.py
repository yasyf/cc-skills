import contextlib
import hashlib
import io
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from drive import agents, brief, cli, dashboard, limits, owing, page, picture, reads, refs, render, slack, state, view
from drive.state import DriveError

SPAN = re.compile(r"\bfor (?:about |~)?(?:(\d+(?:\.\d+)?)(?: ?(m|mins?|minutes?|h|hrs?|hours?))?|(an|half an) hour)\b", re.IGNORECASE)
SPAN_SLACK = timedelta(minutes=5)
CARDS_A_MESSAGE = 5
CARD_KINDS = (("question", "question"), ("tap", "PR card"))
NO_CARDS = "No open cards"
TICKET_FLAGS = {"ticket_id": "--ticket", "ticket_tree": "--ticket-tree", "ticket_agent": "--ticket-agent", "ticket_call": "--ticket-call", "ticket_fingerprint": "--ticket-fingerprint"}
EDIT_TAKES = {"edit", "text", "blocks", "asked", "claims", "dry_run", "done", "record", *TICKET_FLAGS}
RECORD_WAIT_SECONDS = 1.0
RECORD_POLL_SECONDS = 0.05
BARE_TS = re.compile(r"\d{10}\.\d{6}")
WATCH_LOG = "watch.log"


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack, or in a thread outside it named by channel_id, such as an alert's, or with no thread at the root of the channel channel_id names, such as a staging claim to its members, or in the group DM of a person, him and the bot when channel_id is that person's user id, or reword one it made in any of those, and keep the same item on the drive's dashboard, in the words it was given; a post is recorded on the one order cli.post_order decides, never one its caller names, wherever it goes, and a post in a thread is the last line of each open order whose lines go in that thread, whatever order it is recorded on or none, so the watch's clock and each session's overdue line read every line there, and posts nothing from a caller but the communicator, the drive's own posts of reports going through report_line, delivering the parts its report record names, making the clock time its words promise his next line by the order's due, and adding its thread to the order's threads; a post at another channel's root, or a line in its thread, whose report names an end as until keeps that end on its order, from which the watch owes that thread its closing line; a claim at another channel's root goes out only on a report naming an end still to come, which its words name, and a report naming none or a past one goes back to its writer. A question to a teammate naming the job it is for keeps that job waiting on their reply. A report's post names each correction cci holds re it, which the post may lack, and a correction of one a post carried posts nothing, naming where that report's copy is. What the drive's checks find in its words, the say hook hands Jev to judge against his words once this has posted them, before only a post a count or a mark refuses , such as one over its length, a post saying a release may start while a freeze or his release hold it does not name stands, and a dry run."""
    line = spoken(name, mended(post), caller(name))
    if post.get("done") and not post.get("dry_run"):
        line += finished(post.get("asked"))
    return line


ASKED_LINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:\?thread_ts=(\d+\.\d+))?")


def finished(asked: object) -> str:
    """Mark each message of his asked names by permalink done through `cc-slack pickup --done`, so its reaction says the work ended, as the writer judged it did by passing done; what it marked and any it could not."""
    marked, failed = [], []
    for each in slack.messages(asked) if isinstance(asked, (str, list)) else []:
        if not (found := ASKED_LINK.search(each)):
            continue
        channel, ts = found[1], f"{found[2]}.{found[3]}"
        try:
            slack.cc_slack("pickup", "--channel", channel, "--ts", ts, *(["--thread", found[4]] if found[4] and found[4] != ts else []), "--done")
            marked.append(ts)
        except slack.SlackError as error:
            failed.append(f"{ts} ({error})")
    return "".join([f"; marked done {', '.join(marked)}" if marked else "", f"; not marked done {', '.join(failed)}" if failed else ""])


TICKET_FRESH_SECONDS = 120


def with_ticket(arguments: dict) -> dict:
    """The say call's arguments with the ticket cc-slack's permission hook kept for it, when the MCP server handed it none: a server started before the install that reads tickets by tool use id keeps running its old outer loop, since /reload-plugins restarts no stdio server whose command is unchanged, while every call runs here in the newest install; the session's oldest ticket file from the last TICKET_FRESH_SECONDS is this call's, as the hook writes one per call just before it."""
    if "ticket_id" in arguments or not (session := os.environ.get("CLAUDE_CODE_SESSION_ID")):
        return arguments
    from drive import mcp

    fresh = time.time() - TICKET_FRESH_SECONDS
    kept = sorted((path for path in mcp.tickets_dir().glob("*.json") if path.stat().st_mtime >= fresh), key=lambda path: path.stat().st_mtime)
    for path in kept:
        ticket = json.loads(path.read_text())
        if ticket.get("ticket_tree") == session or ticket.get("session") == session:
            path.unlink(missing_ok=True)
            return arguments | {key: value for key, value in ticket.items() if key in TICKET_FLAGS}
    return arguments


def mended(post: dict) -> dict:
    """The post with each bare link worded, as the say hook words it, made here too with no second check-post, since Claude Code keeps one PreToolUse hook's updatedInput and cc-slack's permission hook hands back the words as typed with its ticket, which dropped the say hook's fix on p1794828271074249."""
    from drive import fixes

    if not (text := post.get("text")) or not (bare := fixes.bare_links(text)):
        return post
    for url in bare:
        text = fixes.wrapped(text, url)
    return post | {"text": text}


class Skipped(DriveError):
    """A report REPORT_RULES' Sonnet call wrote no post for, its reason the rule the report broke."""


def report_words(report: dict) -> str:
    """A report's whole words: its text, then the blob cci keeps of its body, or the blob alone when it holds a text past 400 characters whole, which opens with the text's own words."""
    path = (report.get("refs") or {}).get("path")
    if not (path and Path(path).exists()):
        return report["text"]
    kept = Path(path).read_text()
    return kept if kept.startswith(report["text"].removesuffix("…")) else f"{report['text']}\n\n{kept}"


REPORT_RULES = """You write the one Slack post the owner of a software drive reads for a report a session of the drive sent, and decide where it goes. You get the report's words, its attrs, the thread and channel it names, and, for a correction, the post it corrects. Reply with one JSON object and nothing else:

A line saying work he asked for is live, or an incident he was told about is over, always posts, in his thread: never skip one.

{"skip": "<why>"} when it holds no news for him, or asks the poster to decide or do something besides posting, such as answer a teammate, or is a progress line that neither answers a message of his, nor is on an incident, nor is on a cadence order he set, nor reports a finding or the regular progress of work he explicitly asked for (his 03:40 rule: each finding as it happens, a progress line about every 30 minutes), which goes as FYI; the why goes back to its writer, so name the rule it broke; else
{"text": "<the post>", "place": "thread" | "root" | "edit", "blocks": [<Block Kit blocks>], "visuals": [<visuals>]}, blocks and visuals optional.

A page report (its facts name a "page", and "last_posted_to_him" is the glance he was last posted of it, null before the first): post only when the page's status or next step changed, since that last post, in a way he acts on or would want to know; otherwise skip, as the dashboard already shows the page. A plain repeat of the same status, reworded or recounted, is never news. For example:
- last "36 PRs and 20 picks wait on you; approve 20 PRs, then make three picks", now "approve the 18 ready PRs, then make three picks": skip, the same asks in new words.
- a "changed" reading "Nothing new needs you since you last looked": skip.
- last "36 PRs and 20 picks wait on you", now "the release is blocked: its staging check failed, and its re-run needs your tap": post, a new step for him.
- last "waiting on your pick of the writer handover", now "all its orders are done and live": post, its state changed.

Where it goes:
- A reply to his message goes in his message's thread. News nobody asked for goes at the root of its project's channel. A line on an order's own subject goes in the thread its record's thread attr names. A PR card for an incident's fix also goes in the incident's thread.
- "thread": in the thread the report names, a direct follow-up on its subject. "root": a new update, a release step or a decision at the channel's root. "edit": a correction rewords the post it corrects, in that post's own place, keeping its subject; a correction that adds news rather than fixing words goes "thread", under the post it corrects.

""" + render.POST_CRAFT + """

The mechanics of a drive post:
- only links the report prints; a release as {release:<its release name or build number>}, never its CI build link; keep each {record:…|…}, {pr:…}, {card:…} and {order:…} reference exactly as written, a card he must answer as {card:<waits id>} so it draws under the post;
- times as the report writes them in Pacific; a correction says what was wrong and what is right, never "correction" alone;
- earlier reports listed for the same place go in this one post, in the order sent, a later report replacing what it restates or corrects; a passed due is never promised;
- each visual is {"kind": "chart" | "compare" | "mermaid" | "dot", "source": "<its source>", "caption": "<what it shows>"}, one a post; blocks are Block Kit, with text kept as the notification line.
Shape of a reply: {"text": "<the lead>", "place": "root", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*<the lead>*"}}, {"type": "section", "fields": [{"type": "mrkdwn", "text": "*<item>*\\n<its state>"}]}]}
"""


def unposted_before(known: dict, seq: str, channel: str | None, thread: str | None) -> list[tuple[str, str]]:
    """Each report the drive took to post before this one, to the same channel and thread, that has no post yet, oldest first, with its words, so its writer folds them into one post in the order sent, a later report replacing what it restates or corrects, by meta's fix of 736144, where a correction posted before the report it corrected."""
    held = []
    for earlier in sorted((each for each in (known["posts"].get("claimed") or {}) if int(each) < int(seq) and known["carried"].get(each) == agents.DRIVE_CLAIM), key=int):
        read = agents.answered(earlier)
        if (read["attrs"].get("thread") or read["thread"]) == thread and read["attrs"].get("channel", channel) == channel:
            held.append((earlier, report_words(read["report"]).removeprefix("POST").strip()))
    return held


def carried_earlier(name: str, seq: str, earlier: list[str]) -> None:
    """Mark each earlier report the post folded in as posted where it went."""
    if not earlier or not (link := str(state.load(name, ("carried",))["carried"].get(seq) or "")).startswith("https://"):
        return

    def carried_earlier_write(data: dict) -> None:
        for held in earlier:
            data["carried"][held] = link
            data["posts"].get("claimed", {}).pop(held, None)

    state.write(name, carried_earlier_write, patient=True)


def told_stale(name: str, seq: str, writer: str, due: str) -> None:
    """Tell a report's writer its post went out past the due it typed, with that due left off, so it owes a fresh one if a next step is still owed."""
    from drive import tools

    tools.sent(name, "watch", [{"kind": "note", "to": [f"{writer}@{name}"], "re": [seq], "text": f"report {seq} posted after its due {due} passed, so its post left that due out: send a fresh due if a next step is still owed"}])


def report_line(name: str, seq: str) -> str:
    """Post a `POST` report for him from the drive itself, an open incident's report naming no thread in the incident's thread in a channel the drive keeps, by state.incident_place, by his words "Where did the approve tap get sent initially?", from a process the watch starts, or a line the watch writes from its own rows, a milestone, an incident's end or a proved order's live line, from the watch's own pass, so no session posts it and no message of his waits behind one: one Sonnet call given REPORT_RULES, his rules for a post, where it goes and when a visual is owed, writes the post and decides its place, by his words "get rid of hard-coded stuff, give it the rules, and let it make the call", then each mechanical `cc-slack check-post` fix is made by fixes.mended and say posts it citing the report: in the thread the report names, at its channel's root, or as an edit of the post a correction corrects, in that post's own place; each order its close attr names then closes on it, once the post's record lands on its order. A report placed in a channel outside the drive's, where cc-slack posts only on the ticket a session's permission hook names, a link the report does not print, words check-post still faults and a report the model skips, as Skipped, post nothing, and the refusal goes back to its writer, by report_post, or to the watch's pass that called it."""
    from drive import answer, fixes

    read = agents.answered(seq)
    attrs, report = read["attrs"], read["report"]
    known = state.load(name)
    words = report_words(report).removeprefix("POST").strip()
    thread = attrs.get("thread") or read["thread"]
    incident = None if thread or attrs.get("order") not in known["orders"] else state.incident_place(known, [attrs["order"]])
    incident = incident if incident and incident[0] in slack.homes(known) else None
    thread = incident[1] if incident else thread
    channel = attrs.get("channel") or (incident and incident[0]) or (thread and (kept_in(known, thread) or outside(known, thread)))
    if channel and channel not in slack.homes(known):
        raise DriveError(f"its post goes in {channel}, outside the drive's channels, where cc-slack posts only on the ticket a session's permission hook names")
    corrected = known["carried"].get(str(report.get("re") or "")) or "" if report["kind"] == "correction" else edited(channel, attrs.get("edit"), thread)
    earlier = unposted_before(known, seq, channel, thread)
    stale = attrs.get("due") and state.parse_time(attrs["due"]) < state.now() and attrs["due"]
    facts = "\n".join(
        filter(
            None,
            [
                f"Report {seq}, a {report['kind']}:",
                words,
                f"Attrs: {json.dumps(attrs)}",
                f"Thread: {thread}" if thread else "No thread: it goes at the channel's root.",
                f"Channel: {channel}" if channel else "",
                f"The post it corrects: {corrected}" if corrected else "",
                f"Its typed due, {stale}, passed before it could post: post its substance and leave out every promise tied to that time." if stale else "",
                *(["Earlier reports for this same place, sent before it and not yet posted, oldest first:", *(f"- Report {held}: {said}" for held, said in earlier)] if earlier else []),
            ],
        )
    )
    said, _ = answer.ask(facts, "Write his post for this report as one JSON object.", REPORT_RULES, "ask")
    try:
        made = json.loads(said[said.index("{") : said.rindex("}") + 1])
    except ValueError:
        raise DriveError(f"its post was not written: {render.cut(said, 200)}") from None
    if made.get("skip"):
        with (state.drive_dir(name) / WATCH_LOG).open("a") as log:
            log.write(f"{state.stamp()} report {seq} by {report['lane']} skipped by report-post: {made['skip']}\n")
        raise Skipped(f"its post was skipped: {made['skip']}")
    if not (text := (made.get("text") or "").strip()):
        raise DriveError(f"its post was not written: {render.cut(said, 200)}")
    drawn = {link for ref in refs.resolved(name, words).values() for link in (ref.get("url"), ref.get("card"), ref.get("board")) if link}
    if strays := [url for url in answer.URL.findall(json.dumps(made)) if url not in words and url not in corrected and url not in drawn]:
        raise DriveError(f"its post links {strays[0]}, which the report does not print")
    if findings := slack.post_findings(text, bool(made.get("blocks")) and "{card:" in text):
        text = fixes.mended(text, findings, state.now())
    shown = {"text": text} | {key: made[key] for key in ("blocks", "visuals") if made.get(key)}
    if made.get("place") == "edit" or report["kind"] == "correction" and state.CARD_LINK.search(corrected):
        if not corrected.startswith("https://"):
            raise DriveError(f"it corrects report {report.get('re')}, which went out in no post")
        return spoken(name, shown | {"edit": corrected, "record": seq}, known)
    place = {"channel_id": channel} if channel else {}
    if (made.get("place") == "thread" or incident) and (under :=(channel, thread) if thread else corrected and state.link_thread(corrected)):
        place = {"thread_ts": under[1]} | ({"channel_id": under[0]} if under[0] else {})
    owes = {key: attrs[key] for key in (("outcome",) if stale else ("due", "owes", "outcome")) if attrs.get(key)}
    line = spoken(name, shown | {"record": seq} | place | owes, known, recorded_first=bool(state.closed_orders(attrs)))
    carried_earlier(name, seq, [held for held, _ in earlier])
    if stale:
        told_stale(name, seq, report["lane"], stale)
    return line + "".join(closed(name, order_id, seq) for order_id in state.closed_orders(attrs) if state.load(name)["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES)


def edited(channel: str | None, ts: str | None, thread: str | None) -> str:
    """The permalink of the post a report's edit attr names, as a permalink kept as given or by its ts in the report's channel, the post its words reword; empty with no edit attr or channel."""
    if ts and ts.startswith("https://"):
        return ts
    if not (ts and channel):
        return ""
    query = f"?thread_ts={thread}&cid={channel}" if thread and thread != ts else ""
    return f"{slack.whoami()['team_url']}archives/{channel}/p{ts.replace('.', '')}{query}"


def handed_posts(data: dict, seq: str | None, place: tuple[str, str] | None) -> list[str]:
    """The reports handed to the communicator to post outside the drive's channels that this post carries: the one it names as its record, or any whose channel and thread it posts in, since the communicator may post one before its note reaches it, by meta's rule that every path posting a POST records its outcome."""
    handed = data["posts"].get("handed", {})
    return [held for held, where in handed.items() if held == seq or place and place[0] == where["channel"] and where["thread"] in (None, place[1])]


def carry_handed(data: dict, seq: str, link: str) -> None:
    """Mark a handed report posted at link, and the reporter of its order whose thread it went in as told, as `drive order told` does."""
    where = data["posts"]["handed"].pop(seq)
    data["carried"][seq] = link
    data["posts"].get("claimed", {}).pop(seq, None)
    if (order := data["orders"].get(where["order"] or "")) is None:
        return
    for held in order["reporters"]:
        kept = state.kept_record(data, held)[1]
        if (kept["channel"], kept["thread"]) == (where["channel"], where["thread"]) and not kept["told"]:
            kept["told"] = link


REPORT_POST_TRIES = 3
SLACK_TIMED_OUT = ("deadline exceeded", "timed out", "timeout")


def timed_out_again(name: str, seq: str) -> str:
    """report_line, tried again while cc-slack times out and the report has no post yet, so a slow Slack read never drops his post, as 735671's thread read did."""
    for attempt in range(REPORT_POST_TRIES):
        try:
            return report_line(name, seq)
        except slack.SlackError as error:
            if attempt + 1 == REPORT_POST_TRIES or not any(words in str(error) for words in SLACK_TIMED_OUT) or str(state.load(name)["carried"].get(seq) or "").startswith("https://"):
                raise
    raise AssertionError(REPORT_POST_TRIES)


def report_post(name: str, seq: str) -> str:
    """`drive report-post`: hand a report posting in a channel outside the drive's, such as a staging claim to its members, to the communicator as one note, since cc-slack posts there only on the ticket a session's permission hook names; post any other by report_line, or send why it posted nothing back to the session that wrote it, as a refused record re the report, so that session sends it again fixed; one the watch wrote goes to the meta session as a defect."""
    from drive import tools

    if (link := state.load(name)["carried"].get(seq) or "").startswith("https://"):
        return f"report {seq} already posted at {link}: nothing posted again"
    read = agents.answered(seq)
    target = read["attrs"].get("channel") or ((thread := read["attrs"].get("thread") or read["thread"]) and outside(state.load(name), thread))
    if target and target not in slack.homes(state.load(name)):
        handed_thread = read["attrs"].get("thread") or read["thread"]
        state.write(name, lambda data: data["posts"].setdefault("handed", {}).__setitem__(seq, {"channel": target, "thread": handed_thread, "order": read["attrs"].get("order")}), patient=True)
        agents.handle(name, f"Record: {seq} Drive: {name} Scratch: {state.drive_dir(name) / 'scratch' / f'post-{seq}'} Where: {target}, a channel outside the drive's, whose members read it and where cc-slack posts only on the ticket a session's permission hook names, so post it there yourself with say naming record {seq}, which marks it posted so the drive posts it never again")
        return f"report {seq} handed to the communicator: it posts in {target}, outside the drive's channels"
    try:
        return f"report {seq} posted by the drive: {timed_out_again(name, seq)}"
    except (DriveError, slack.SlackError) as error:
        if (link := state.load(name)["carried"].get(seq) or "").startswith("https://"):
            return f"report {seq} posted at {link}, then: {error}"
        writer = agents.shown(seq)["lane"]
        made = {"kind": "defect", "to": [f"meta@{name}"]} if writer == "watch" else {"kind": "refused", "re": [seq], "to": [f"{writer}@{name}"]}
        said = f"report {seq} is not posted, by the drive or the communicator, as the drive's carried posts read: {error}"
        tools.sent(name, "watch", [limits.shortened(made | {"text": said}) or made | {"text": said}])
        sent_back = f"sent back to {made['to'][0]}"

        def sent_back_write(data: dict) -> None:
            data["carried"][seq] = sent_back
            data["posts"].get("claimed", {}).pop(seq, None)

        state.write(name, sent_back_write)
        return f"report {seq} sent back to {made['to'][0]}: {error}"


def spoken(name: str, post: dict, known: dict, recorded_first: bool = False) -> str:
    """Post one message as say does, once its caller may: known is the drive as say's caller read it; recorded_first waits for the post's record on its order, as a close does, for a caller that closes the order next; a close of an order another report already closes is dropped and the post goes out, warning once per order, since a gate blocks at most once."""
    report = cli.report_attrs(post["record"]) if post.get("record") else {}
    post = placed(known, post, report)
    order = cli.post_order(known, post, report)
    if close := bool(order and cli.answers_order(known, order, post)):
        post = {key: value for key, value in post.items() if key != "record"}
        report = {"close": order, "writer": "communicator", "written": state.stamp()}
    seq = report.get("seq")
    closed_again = None
    if order and (again := state.second_close(known, order, report)):
        report = report | {"close": ", ".join(named for named in state.closed_orders(report) if named != order)}
        close = False
        closed_again = None if order in known["posts"].get("closed_again", []) else (order, again.removeprefix("refused, ").replace(", so this second close of it posts nothing: send no close of", "; the post went out without its close of"))
    if refused := carried_refusal(known, seq, report, post.get("edit")) or (order and cli.report_refusal(known, order, report)) or (close and cli.unfiled_refusal(known, order)):
        raise DriveError(f"nothing posted: {refused}")
    if refused := unclaimed(known, post, report):
        raise DriveError(f"nothing posted: {refused}; {returned(name, post, report, refused)}")
    if refused := unended(known, post, report):
        raise DriveError(f"nothing posted: {refused}")
    if refused := brief.claim_refusal(post.get("text") or "", name, known):
        raise DriveError(f"nothing posted: {refused}")
    if post.get("dry_run"):
        return dry(name, post) + (f"; {cli.delivering(name, order, report)}" if order else "")
    amended = agents.amendments(name, [seq] if seq else [])
    if close:
        report["seq"] = seq = close_report(name, order, post.get("text") or "")
    claimed = cli.claim_close(name, report)
    if key := request_key(name, post, report):
        post = post | {"request_key": key}
    try:
        reply = posted(name, post, order, {"record": seq, "report": report if close else None} if order else None)
    except (DriveError, slack.SlackError):
        cli.unclaim_close(name, claimed)
        raise
    link = reply["permalink"]
    line = f"{'EDITED' if post.get('edit') else 'POSTED'} {link}"
    if closed_again:
        state.write(name, lambda data: data["posts"].setdefault("closed_again", []).append(closed_again[0]), patient=True)
        line += f" WARNING {closed_again[1]}"
    if dropped := reply.get("dropped"):
        line += f"; dropped {', '.join(dropped)}, already shown in this thread or its root"
    if card := reply.get("card_permalink"):
        line += f" CARD {card}"
    if job_id := post.get("job"):
        line += "; " + awaiting(name, job_id, link)
    place = None if post.get("edit") else state.link_thread(link)
    if order and not post.get("edit"):
        due = post.get("due") and state.parse_time(post["due"])

        def owed_write(data: dict) -> str | None:
            return owing.posted(data, order, link, due, post.get("owes"), [each.strip() for each in str(post.get("outcome") or "").split(",") if each.strip()], state.now())

        if item_id := state.write(name, owed_write, patient=True):
            line += f"; owes {item_id} by {render.when(state.stamp(due))}"
    if seq or place:
        def spoken_write(data: dict):
            if seq:
                data["carried"][seq] = link
            for handed in handed_posts(data, seq, place):
                carry_handed(data, handed, link)
            if place:
                at = state.stamp(slack.sent_at(slack.posted(link, "")))
                for each in state.open_orders(data).values():
                    if (promised := state.promise_thread(data, each)) == place or promised and promised[0] == place[0] != slack.dm():
                        each["last_line"] = max(each["last_line"] or at, at, key=state.parse_time)

        state.write(name, spoken_write, patient=True)
    if warning := reply.get("warning"):
        line += f" WARNING {warning}"
    if post.get("blocks") and (unshown_blocks := drawn_as(link, post["blocks"])):
        line += f" WARNING {unshown_blocks}"
    if found := amended["correction"].get(seq):
        line += f"; cci holds corrections of report {seq}, {', '.join(found)}: unless this post carries each, reword it with say(edit={link}, text=…)"
    if order:
        line += "; " + record_line(order, *reply["recorder"], close or recorded_first)
    if close:
        line += closed(name, order, report["seq"])
        if warned := owing.close_warning(state.load(name)["orders"][order], order):
            line += f"; {warned}"
    return line


def caller(name: str) -> dict:
    """The drive once say's caller reads as the communicator; any other caller posts nothing."""
    known = state.load(name)
    if refused := cli.recording_refusal(known, f"only the communicator posts to him: send these words as a report record to communicator@{name}, which posts and records them"):
        raise DriveError(f"nothing posted: {refused}")
    if seat := state.communicator_project(known, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        stamp = state.stamp()

        def active_write(data: dict) -> None:
            if seat in data["communicators"]:
                data["communicators"][seat]["heard"] = stamp

        state.write(name, active_write)
    return known


def request_key(name: str, post: dict, report: dict) -> str | None:
    """The key cc-slack stamps on each message of the post and reads Slack for before it posts, so a say retried after cc-slack posted and then failed, as dm-status does when its deadline passes while the daemon still posts, gets back what Slack holds instead of posting it again, while a post in other words goes out: a record's post keys on the record, when cci wrote it and the post's words; an edit or any other post has none, and cc-slack keys it per call."""
    if post.get("edit"):
        return None
    words = hashlib.sha256(json.dumps([post.get(field) for field in ("text", "card", "tap_prs", "visuals")]).encode()).hexdigest()[:16]
    if post.get("record"):
        return f"{int(state.parse_time(report['written']).timestamp() * 1000)}-{name}.{post['record']}.{words}"
    return None


def awaiting(name: str, job_id: str, link: str) -> str:
    """Keep the job waiting on the reply to the question a post at link asked a teammate for it, which the watch reads each minute until the first message after it from anyone but the drive and a bot frees the job."""
    def awaiting_write(data: dict):
        job = data["jobs"][job_id]
        job["waits"] = list(dict.fromkeys([*job["waits"], f"reply:{link}"]))

    state.write(name, awaiting_write, patient=True)
    return f"{job_id} waits on the reply to it"


def recording(name: str, receipt: dict) -> tuple[Path, int]:
    """Keep the receipt of a post on its order in the drive and record the post there in its own process, which outlives the say call and waits out the drive's database, so a say killed, or held behind another write, after cc-slack returned its post still records it: the receipt, where that process writes the line it ends with, and its pid. say refused, before it posted, a post its session may not record on the order, so the process, which runs as no session, records every post it is handed."""
    kept = state.drive_dir(name) / "posts" / f"{uuid.uuid4().hex}.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(receipt))
    return kept, state.detach(name, ["say-record", str(kept)], "posts.log")


def record_line(order: str, kept: Path, recorder: int, close: bool) -> str:
    """The line the record of a say post on order ends with, read from its receipt: once it lands within RECORD_WAIT_SECONDS, so a slow Slack read, Jev call or database write never holds the say call past that, else where the receipt keeps it; a post closing its order waits for its recorder to end, so the order takes the post before it is set done."""
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
    """Record a say post on its order from the receipt `recording` kept, waiting out the drive's database however long another write holds it, since the post is already out, and writing the line it ends with back into the receipt; one that cannot be recorded is sent to the meta session as a defect naming the receipt."""
    receipt = json.loads(kept.read_text())
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


def closed(name: str, order_id: str, seq: str) -> str:
    """Close the order a post answered on its report seq; a step that fails is named, since the post is already out."""
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            cli.order_set(cli.parser().parse_args(["order", "set", order_id, "--state", "done", "--record", seq, "--drive", name]))
        except DriveError as error:
            return f"; {order_id} not closed on report {seq}: {error}"
    return f"; {order_id} closed on report {seq}"


def carried_refusal(data: dict, seq: str | None, report: dict, edit: str | None = None) -> str | None:
    """Why a correction's post goes nowhere: the report it corrects went out in a post, which the correction rewords; none for the edit of that very post, which say marks the correction posted at."""
    if (post := data["carried"].get(report.get("corrects"))) and state.CARD_LINK.search(post) and not same_message(post, edit):
        return f"record {seq} corrects report {report['corrects']}, which went out in {post}: reword that post with say(edit={post}, text=…)"
    return None


def same_message(one: str, other: str | None) -> bool:
    """Whether two Slack permalinks name one message, whatever thread or cid query each carries."""
    return bool(other and (found := POSTED_TS.search(one)) and (again := POSTED_TS.search(other)) and found.groups() == again.groups() and one.split("/p")[0] == other.split("/p")[0])


def outage(data: dict, post: dict, report: dict, order_id: str | None) -> bool:
    """Whether a post reports on an outage he hears at once, never as its words read: an incident, as the order it goes to marks it, or a customer team's outage the drive did not cause, as its report's customer_outage mark says."""
    return report.get("customer_outage") == "true" or bool((posted := posted_order(data, post, order_id)) and data["orders"][posted]["incident"])


def posted_order(data: dict, post: dict, order_id: str | None) -> str | None:
    """The order a post goes to: the one say records it on, else the open order whose own thread, or the thread its lines to him go in, it goes in, by the thread's ts, so a bare thread_ts of a channel the drive keeps finds it too."""
    thread = place(post)[1]
    return order_id or next((each for each, order in state.open_orders(data).items() if thread and thread in {where[1] for where in (state.own_thread(order), state.promise_thread(data, order)) if where}), None)


def waiting_posts(name: str, data: dict, found: dict, moment: datetime) -> dict[str, str]:
    """The words of the one post in each channel of the drive, his DM for a drive keeping none, that says what waits on him there, his DM's post kept before the drive kept channels reading NO_CARDS once they do, which the watch edits in place of asking any card again: a count of the open cards the status page queues whose project posts there, linking that queue on the dashboard, then the first five, which Slack draws under the post, those carrying out an order whose bug hurts someone now first, else in the queue's order; NO_CARDS where none waits. The drive's own channel's post opens with one line per project channel, linking it and that project's queue on the dashboard with how many cards wait there, by his words "the drive channel carries one line per project", so it reads as the drive's index."""
    queued = [item for item in view.build(data, found, moment)["queue"] if "card" in item]
    held = data["slack"]["channels"]
    here: dict[str, list[dict]] = {channel: [] for channel in sorted({made["id"] for made in held.values()} or {slack.dm()})}
    for item in sorted(queued, key=lambda item: not any(state.hurts_now(data, data["orders"][order_id]) for order_id in item["tasks"] if order_id in data["orders"])):
        here.setdefault(queue_channel(data, item), []).append(item)
    board = dashboard.link(name)
    projects = {made["id"]: project for project, made in held.items() if project}
    words = {
        channel: "\n".join(
            [
                f"*<{board if channel == slack.dm() else page.seat_query(board, data, items[0]['project'])}{'#/queue/all' if channel == slack.dm() else ''}|Waiting on you: {card_counts(items)}>*",
                *(f"{{card:{item['card']['waits']}}}" for item in items[:CARDS_A_MESSAGE]),
            ]
        )
        if items
        else NO_CARDS
        for channel, items in here.items()
    }
    if "" in held:
        index = [f"*<{board}#/|{name}>* projects:", *(f"<#{channel}> · <{page.seat_query(board, data, project)}|{card_counts(here[channel]) or 'nothing'} waiting>" for channel, project in projects.items())]
        own = words[held[""]["id"]]
        words[held[""]["id"]] = "\n".join([*index, *([] if own == NO_CARDS else ["", own])])
    if held and slack.dm() in data["slack"]["waiting"]:
        words.setdefault(slack.dm(), NO_CARDS)
    return words


def queue_channel(data: dict, item: dict) -> str:
    """The channel a card the status page queues waits on him in: that of the seat of the first order it carries out, by project, else of its own project, by slack.project_channel."""
    return slack.project_channel(data, project(data, {}, [], next((order_id for order_id in item["tasks"] if order_id in data["orders"]), None)) or item["project"])


def card_counts(queued: list[dict]) -> str:
    return ", ".join(f"{count} {word}{'' if count == 1 else 's'}" for kind, word in CARD_KINDS if (count := sum(item["kind"] == kind for item in queued)))


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
    """Refuse an edit carrying anything but new words for its post, or a fence that draws as a picture, which an edit of a Slack message cannot add, a PR card naming a PR the drive holds none of, which it cannot draw, a post outside his DM naming his DM, carrying about, which names a card his reply in his DM is about, or drawing a card with no thread, and a job a question is for named on a post in his DM, on a card or on a job finished or unknown."""
    if channel := post.get("channel_id"):
        if channel == slack.dm():
            raise DriveError(f"nothing posted: channel_id {channel} is his DM; leave channel_id out, and say posts there through cc-slack dm-status")
        if post.get("about"):
            raise DriveError("nothing posted: about names the card a reply in his DM is about, which cc-slack puts in that card's thread there, so a post with channel_id takes none")
        if not post.get("thread_ts") and (post.get("card") or post.get("tap_prs")) and channel not in slack.homes(state.load(name, ("slack",))):
            raise DriveError(f"nothing posted: a card goes up in his DM and its line in a thread outside it, so with channel_id {channel} it takes that thread's thread_ts")
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
        jobs = state.load(name, ("jobs",))["jobs"]
        if job_id not in jobs or jobs[job_id]["state"] in state.FINISHED_JOB_STATES:
            held = jobs[job_id]["state"] if job_id in jobs else "no job of this drive"
            raise DriveError(f"nothing posted: job {job_id} is {held}, so nothing would wait on the reply")
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    if stack:
        prs = state.load(name, ("prs",))["prs"]
        if strays := [key for key in stack if key not in prs]:
            raise DriveError(f"tap_prs names {', '.join(strays)}, which the drive holds no PR for, so nothing was posted: drive pr add records it")


def placed(data: dict, post: dict, report: dict) -> dict:
    """The post where its caller names it: a bare thread_ts of an order's thread in a channel the drive keeps, or outside his DM such as an alert's, with that channel filled in, and a report's post naming no place in the thread and channel its record names, as its writer chose them."""
    if post.get("edit") or post.get("channel_id"):
        return post
    if thread := post.get("thread_ts"):
        return post | ({"channel_id": channel} if (channel := outside(data, thread) or kept_in(data, thread)) else {})
    if (thread := report.get("thread")) and not any(post.get(key) for key in ("about", "card", "tap_prs")):
        return post | {"thread_ts": thread} | ({"channel_id": channel} if (channel := report.get("channel") or outside(data, thread) or kept_in(data, thread)) else {})
    return post


def unclaimed(data: dict, post: dict, report: dict) -> str | None:
    """Why a post to a channel's members goes back to the writer of its report: the repository's staging-claim rule has each claim say how long it holds, so a post at the root of a channel outside his DM, such as a staging claim, goes out only on a report whose until attr names that end, and a claim or a line in its thread moving it only while that end is still to come; the end is the writer's to set, never the communicator's to drop."""
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
    """The channel outside his DM, its operator channels and the channels the drive keeps a thread of an order is in, its own or one it keeps, such as an alert's; None for a thread of those."""
    homes = slack.homes(data)
    return next((where[0] for order in data["orders"].values() for where in [state.own_thread(order), *order["threads"]] if where and where[1] == thread and where[0] not in homes), None)


def kept_in(data: dict, thread: str) -> str | None:
    """The channel the drive keeps that a thread of an order is in, its own or one it keeps, by its ts; None for a thread in none of them."""
    held = {made["id"] for made in data["slack"]["channels"].values()}
    return next((where[0] for order in data["orders"].values() for where in [state.own_thread(order), *order["threads"]] if where and where[1] == thread and where[0] in held), None)


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
    post = post | {"text": state.provided(state.load(name, ("name", "roles", "subcoordinators", "communicators"))).transformed(refs.carded(name, post["text"]))} if post.get("text") else post
    text = post.get("text") or ""
    blocks = json.loads(post["blocks"]) if isinstance(post.get("blocks"), str) else post.get("blocks")
    found = refs.resolved(name, "\n".join([text, *block_words(blocks)]))
    if not post.get("edit") and (seq := post.get("record")) and (link := str(state.load(name, ("carried",))["carried"].get(seq) or "")).startswith("https://"):
        raise DriveError(f"report {seq} went out meanwhile at {link}, so it posts nothing again")
    check(name, post)
    visuals, words, lifted = shown(name, post)
    card = post.get("card")
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    data = state.load(name)
    dm = slack.dm()
    sent = refs.slack(words, found, shown_in(name, post))
    if blocks:
        post = post | {"blocks": linked_blocks(blocks, found, shown_in(name, post))}
    if leftover := unrendered([sent, *block_words(post.get("blocks"))]):
        raise DriveError(f"nothing posted: its words would show {leftover!r} as typed, a reference or stub say draws nothing for; name it as say's tool description does, or drop it")
    pictures = [*(visual["picture"] for visual in visuals if "picture" in visual), *lifted]
    asked = cli.asked_on(name, card["waits"]) if card and card.get("waits") else set()
    standing = None if card or post.get("edit") else asked_already(name, data, stack)
    drawn = bool(card or stack)
    first, *rest = places(data, post, stack)
    first = homed(name, data, post, stack, order, first)
    pictures, dropped = unshown(name, first, pictures)
    operators = slack.homes(data)
    reply = slack.cc_slack(
        *(
            ["edit", "--url", post["edit"], "--text", sent, *blocked(post), *ticketed(post)]
            if post.get("edit")
            else replied(post, pictures, sent)
            if first[0] not in operators
            else status(name, data, post | {"channel_id": first[0], "thread_ts": first[1]}, [], pictures, card_link({"permalink": standing}, sent, "; ".join(view.pr_title(data["prs"][key]) for key in stack))[1])
            if standing
            else status(name, data, post | {"channel_id": first[0], "thread_ts": first[1]}, stack, pictures, sent)
        )
    )
    if standing:
        reply |= {"card_permalink": standing}
    shown_pictures(name, (first[0], first[1] or state.link_thread(reply["permalink"])[1]), pictures)
    if dropped:
        reply |= {"dropped": dropped}
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
        def posted_write(data: dict):
            state.join_pr_cards(data, stack, [reply.get("card_permalink")], state.stamp())

        state.write(name, posted_write, patient=True)
    for place in [place for place in rest if place[0] not in operators]:
        reply |= {"permalink": carried(name, reply, place, post, refs.slack(words, found, place[1]), label, kept, stack)}
    if unlinked := [ref["warning"] for ref in found.values() if ref.get("warning")]:
        reply |= {"warning": "; ".join(filter(None, [reply.get("warning"), *unlinked]))}
    return reply


SHOWN_PICTURES = "pictures-shown.json"
SHOWN_FOR = timedelta(days=2)


def picture_hash(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def shown_hashes(name: str) -> dict[str, dict[str, str]]:
    """Each thread's picture content hashes, by channel/thread, with when each was shown, kept SHOWN_FOR."""
    path = state.drive_dir(name) / SHOWN_PICTURES
    kept = json.loads(path.read_text()) if path.exists() else {}
    cutoff = (datetime.now(UTC) - SHOWN_FOR).isoformat()
    return {thread: fresh for thread, hashes in kept.items() if (fresh := {each: at for each, at in hashes.items() if at >= cutoff})}


def unshown(name: str, where: tuple[str, str | None], pictures: list[str]) -> tuple[list[str], list[str]]:
    """The pictures a post keeps and the names of those it drops: one whose content hash already shows in the thread it goes in or that thread's root, by his words "re-posted the same image in the thread, it was good in the main post but no reason to re-post the same image"; a post at a root drops none."""
    if not where[1] or not pictures:
        return pictures, []
    seen = shown_hashes(name).get(f"{where[0]}/{where[1]}", {})
    kept = [path for path in pictures if picture_hash(path) not in seen]
    return kept, [Path(path).name for path in pictures if path not in kept]


def shown_pictures(name: str, where: tuple[str, str | None], pictures: list[str]) -> None:
    """Keep each picture a post showed under its thread, a root post's under the thread it opens."""
    if not where[1] or not pictures:
        return
    kept = shown_hashes(name)
    at = datetime.now(UTC).isoformat()
    kept.setdefault(f"{where[0]}/{where[1]}", {}).update({picture_hash(path): at for path in pictures})
    (state.drive_dir(name) / SHOWN_PICTURES).write_text(json.dumps(kept))


def asked_already(name: str, data: dict, stack: list[str]) -> str | None:
    """The link of his one open card already asking his tap on every PR a post names in tap_prs at its head, none of their stack mates left off it, by view.apart: the post links that card in place of tap_prs, so a line that asks again what he has not answered moves no card and marks none replaced, and only an ask that changed, a new head, PR or Hold, draws one."""
    found = view.live_cards(name) if stack else None
    links = {view.tap_card(data, data["prs"][key], found) for key in stack}
    if found is None or len(links) != 1 or None in links or any(view.apart(data, key, found["pr_cards"]) for key in stack):
        return None
    return links.pop()


def homed(name: str, data: dict, post: dict, stack: list[str], order: str | None, where: tuple[str, str | None]) -> tuple[str, str | None]:
    """Where a post naming no channel and no thread goes while the drive keeps channels, by his words "everything should be out of DMs now for drive stuff": an answer to a message of his that asked names goes in that message's thread, a card in the thread its order's lines go in in its project's channel, and anything else at the root of its project's channel; a post naming a place, an edit, and a drive keeping no channel post where they name."""
    if where != (slack.dm(), None) or post.get("edit") or not data["slack"]["channels"]:
        return where
    if answered_ts(data, post):
        return his_thread(post)
    on = order or posted_order(data, post, None)
    channel = channel_for(name, data, project(data, post, stack, on))
    thread = state.promise_thread(data, data["orders"][on]) if on and (post.get("card") or post.get("about") or stack) else None
    return channel, thread[1] if thread and thread[0] == channel else None


def his_thread(post: dict) -> tuple[str, str]:
    """The channel and thread of the first message of his a post's asked names by ts or permalink: a bare ts is its own thread in his DM, and a permalink names its message's channel and thread, or the message itself at a root."""
    for entry in slack.messages(post["asked"]):
        if BARE_TS.fullmatch(entry):
            return slack.dm(), entry
        if message := state.slack_message(entry):
            return message[0], message[2] or message[1]
    raise DriveError("asked names no message of his by ts or permalink")


def answered_ts(data: dict, post: dict) -> list[str]:
    """The ts of each message of his a post answers, by its asked, which the watch's daily sweep times his answers by: a ts or a permalink names its own message; an edit answers none."""
    asked = [] if post.get("edit") else slack.messages(post.get("asked") or "")
    named = [entry if BARE_TS.fullmatch(entry) else message and message[1] for entry in asked for message in [state.slack_message(entry)]]
    return list(dict.fromkeys(filter(None, named)))


def keep_line(name: str, thread: str, link: str, sent: str) -> None:
    """Keep a post in a thread as the line that thread holds about each card of his its words link, on each question drawn on that card, by the thread, an edit's words in its post's place: `drive job set --told` reads the line it names."""
    def keep_line_write(data: dict):
        for _, question in view.drawn_on(data, *view.SLACK_TS.findall(sent)):
            question["told"][thread] = {"link": link, "words": sent}

    state.write(name, keep_line_write, patient=True)


def channel_for(name: str, data: dict, project: str | None) -> str:
    """The channel a root post about the project goes in, by slack.project_channel, first making the project's own by cli.open_channel once the drive has its own channel and the project has none, by his words "sub-channels one for each project/communicator, private, created on demand"."""
    if (filed := slack.channel_project(project)) and "" in data["slack"]["channels"] and filed not in data["slack"]["channels"]:
        made = cli.open_channel(name, filed)
        data["slack"]["channels"][filed] = data["slack"]["channels"].get(filed, {}) | {"id": made["id"], "name": made["name"]}
    return slack.project_channel(data, project)


def project(data: dict, post: dict, stack: list[str], order: str | None) -> str | None:
    """The sub-coordinator seat a post at the root is about, by state.coordinator_seat, so a priority order's goes in its seat's own channel once that seat is bound, by his pick "1 but also 1 per sub communicator including priority ones and then one central one for the drive": its order's, else that of the order of the job its card asks for, else that of the first order its PRs serve that names one, else for a card the drive's tooling project, so a card of no project asks in the drive's own operator channel."""
    waits = (post.get("card") or {}).get("waits")
    orders = [order] if order else [data["jobs"][job]["order"]] if waits and (job := state.waits_job(data, waits)) in data["jobs"] else [each for key in stack for each in state.served_orders(data, data["prs"][key])]
    return next((seat for each in orders if each in data["orders"] and (seat := state.channel_seat_of(data, data["orders"][each]))), cli.TOOLING_FILING[0] if post.get("card") or stack else None)


def places(data: dict, post: dict, stack: list[str]) -> list[tuple[str, str | None]]:
    """Each channel and thread the post goes in, (his DM, None) for his DM root, (a channel outside it, None) for that channel's root and (a person's user id, None) for the group DM of that person, him and the bot: the one it names; for a card, which cc-slack dm-status draws in his DM or an operator channel alone, first each place in those, the one it names, then for a PR card each thread of the orders its PRs carry out, else his DM root, then the one outside those it names, such as an alert's thread, which takes the card's line, since cc-slack's Slack permission hook judges a say for the place it names alone."""
    dm, operators = slack.dm(), slack.homes(data)
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
    if channel in slack.homes(data) or cli.SLACK_USER.fullmatch(channel):
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
    """The `cc-slack dm-status` call making a new post in his DM, or in the operator channel channel_id names, as the session holding it, so his replies under a post a coordinator, the meta session or the watch made reach the communicator, while the drive's own answer of his message, by answering, posts as the holder's session without --as-holder, so it answers his message there, each PR its card taps linked from data, the drive read before the post. A post made as the holder answers none of his messages above it in cc-slack, so each stays owed until a post answering it names it as asked."""
    argv = ["dm-status", "--agent", "claude", "--drive", name]
    if holder := post.get("answering"):
        argv += ["--session", holder]
    else:
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
    argv += blocked(post) + keyed(post)
    return [*argv, "--", sent] if sent else argv


def draw(name: str, keys: list[str]) -> str:
    """Draw his card on the PRs, as drive pr add draws it on the row it makes and the watch on a PR when it first needs his tap: cc-slack redraws the card where it sits, offering Approve again when drive card still asks it, or first posts it in the thread of the first order they carry out in his DM or an operator channel, at the root of their project's operator channel when they carry out none, his DM's when that project has none, and posts nothing else. Keep it as a said event and store his cards as cc-slack lists them now, which view.tap_card reads; the card's link. A card drive card refuses for every one of them, or a card whose waits id names a card he answered while no PR on it changed since, so his answer stands, posts nothing and says why; a post cc-slack named no card under is kept and said, so it is not posted again."""
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
    channel, thread = next((where for where in state.pr_threads(data, keys) if where[0] in slack.homes(data)), (channel_for(name, data, project(data, {}, keys, None)), None))
    reply = slack.cc_slack(*status(name, data, {"channel_id": channel, "thread_ts": thread}, keys, [], ""))
    link = next(iter((reply.get("card_permalink") or "").split()), None)
    state.append_event(name, "said", text="; ".join(row["title"] for each in drawn["cards"] for row in each["card"]["prs"]), channel=None if channel == slack.dm() else channel, thread=reply.get("thread_ts"), url=reply["permalink"], card=link, order=None, visuals=[], prs=keys, edited=False)
    view.refresh_cards(name)
    if not link:
        raise DriveError(f"posted {reply['permalink']}, so post it not again, but cc-slack named no card of {', '.join(keys)} under it: {reply.get('warning') or 'it gave no warning'}")
    return link


def withdraw(name: str, key: str) -> list[str]:
    """Withdraw each open card of his carrying a PR reworked to his words, his words its reason, linked to the message he said them in, so no card asks his tap on it until its builder's READY says how they are met and the watch draws a fresh one, or carrying a PR waiting on orders he has not scheduled, by state.unscheduled_waits, their titles its reason, until he schedules them and the watch draws a fresh one, and redraw one withdrawn under another reason; each card withdrawn."""
    data = state.load(name)
    pr = data["prs"][key]
    reason, link = (state.rework_reason(pr), pr["rework"]["link"]) if pr["rework"] else (unscheduled_reason(data, state.unscheduled_waits(data, pr), "A fresh card asks your tap"), "")
    withdrawn = slack.withdraw_pr_card(pr["url"], reason, link if limits.PAGE_LINK.fullmatch(link) else "")
    view.refresh_cards(name)
    return withdrawn


def unscheduled_reason(data: dict, steps: list[str], asks: str) -> str:
    titles = ", ".join(f'"{view.order_title(data["orders"][step])}"' for step in steps)
    return f"Waits on your order {titles}, which you have not scheduled. {asks} once you do"


def replied(post: dict, pictures: list[str], sent: str) -> list[str]:
    """The cc-slack call making a new post outside his DM, with the ticket cc-slack's Slack permission hook attached to the say call: `cc-slack reply` in a thread, or with no thread `cc-slack send` at the root of a channel, or to a person's user id, which opens the group DM of that person, him and the bot."""
    thread = ["--thread", post["thread_ts"]] if post.get("thread_ts") else []
    argv = ["reply" if thread else "send", "--agent", "claude", "--channel", post["channel_id"], *thread, "--text", sent]
    argv += [flag for path in pictures for flag in ("--file", path)]
    return argv + blocked(post) + keyed(post) + ticketed(post)


POSTED_TS = re.compile(r"/p(\d{10})(\d{6})")


def drawn_as(link: str, blocks: object) -> str | None:
    """What Slack shows of the blocks a post sent, read back from the message itself: None when it draws each kind of block sent, else which it dropped or that it shows only the text, so the writer redraws it with say(edit=…), by his words "not using Block Kit properly, the checkmarks aren't rendering"."""
    sent = [block.get("type") for block in (json.loads(blocks) if isinstance(blocks, str) else blocks) if isinstance(block, dict)]
    ts = ".".join(POSTED_TS.search(link).groups())
    try:
        read = slack.cc_slack("thread", "--raw", "--url", link)
    except slack.SlackError as error:
        return f"its blocks were not read back to check how Slack draws them: {error}"
    message = next((each for each in read.get("messages") or [] if each.get("ts") == ts), None)
    shown = [block.get("type") for block in ((message or {}).get("raw") or {}).get("blocks") or []]
    if not shown or shown == ["rich_text"] != sent:
        return f"Slack shows this post as its text alone, none of its {', '.join(dict.fromkeys(sent))} blocks: fix them and redraw it with say(edit={link}, …)"
    if dropped := [kind for kind in dict.fromkeys(sent) if kind not in shown]:
        return f"Slack dropped its {', '.join(dropped)} blocks: fix them and redraw it with say(edit={link}, …)"
    return None


STUB = re.compile(r"^\W*\(?(?:its facts|the facts|facts|source|record)\)?\W*$", re.IGNORECASE)


def block_words(blocks: object) -> list[str]:
    """Each mrkdwn or plain text a Block Kit array holds, wherever it nests."""
    if isinstance(blocks, dict):
        return [blocks["text"]] if isinstance(blocks.get("text"), str) and blocks.get("type") in ("mrkdwn", "plain_text", "raw_text", "text") else [word for value in blocks.values() for word in block_words(value)]
    if isinstance(blocks, list):
        return [word for value in blocks for word in block_words(value)]
    return []


def linked_blocks(blocks: object, found: dict[str, dict], thread: str | None) -> object:
    """The blocks with each reference in their mrkdwn linked as say links the post's text, so a block never shows a {pr:} as typed."""
    if isinstance(blocks, dict):
        if blocks.get("type") == "mrkdwn" and isinstance(blocks.get("text"), str):
            return blocks | {"text": refs.slack(blocks["text"], found, thread)}
        if blocks.get("type") in ("plain_text", "raw_text", "text") and isinstance(blocks.get("text"), str):
            return blocks | {"text": re.sub(r"<[^|<>]+\|([^<>]+)>", r"\1", refs.slack(blocks["text"], found, thread))}
        return {key: linked_blocks(value, found, thread) for key, value in blocks.items()}
    if isinstance(blocks, list):
        return [linked_blocks(value, found, thread) for value in blocks]
    return blocks


def unrendered(texts: list[str]) -> str | None:
    """The first reference token or stub line words about to go to Slack still hold: a {kind:…} say draws nothing for, or a line such as "(its facts)" that says nothing, by 732252, where a drive post showed {pr:56973} and {order:o1339} as typed."""
    for text in texts:
        if token := re.search(r"\{(?:card|pr|order|record|answer|started|at|release):[^{}]*\}", text):
            return token[0]
        if stub := next((line for line in text.splitlines() if line.strip() and STUB.match(line)), None):
            return stub.strip()
    return None


def blocked(post: dict) -> list[str]:
    """The flag handing cc-slack the post's Block Kit blocks, which Slack draws as the message while its text stays the notification and the fallback."""
    if not (blocks := post.get("blocks")):
        return []
    return ["--blocks", blocks if isinstance(blocks, str) else json.dumps(blocks)]


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
