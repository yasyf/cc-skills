import contextlib
import html
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from drive import agents, brief, cli, dashboard, picture, reads, refs, render, slack, state, view
from drive.state import DriveError

CATCH_UP_TITLE = re.compile(r"\A\s*\**\s*catch-up since\b[^\n]*\n*", re.IGNORECASE)
CARDS_A_MESSAGE = 5
CARD_KINDS = (("question", "question"), ("tap", "PR card"))
HELD = "report {seq} held for his catch-up, since he is away; drive status lists it until the catch-up posts"
TICKET_FLAGS = {"ticket_id": "--ticket", "ticket_tree": "--ticket-tree", "ticket_agent": "--ticket-agent", "ticket_call": "--ticket-call", "ticket_fingerprint": "--ticket-fingerprint"}
EDIT_TAKES = {"edit", "text", "asked", "claims", "dry_run", *TICKET_FLAGS}
OPENING_WORDS = 12
RECENT_POSTS = 50
RECORD_WAIT_SECONDS = 1.0
RECORD_POLL_SECONDS = 0.05
WORD = re.compile(r"\w+")
BREAKS = "breaks"
PLAIN_QUESTION = (
    "The drive posts this line over his PR card, written from the PR's title, and `cc-slack check-post` found each of these in it. Does the rule"
    " any finding stands for hold against the line, a finding matching words its rule never meant included? The rule: {rule}"
)


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack, or in a thread outside it named by channel_id, such as an alert's, or with a person's user id as channel_id and no thread in the group DM of that person, him and the bot, or reword one it made in any of those, and keep the same item on the drive's dashboard, in the words it was given, a catch-up's title, the count of his open cards it closes with and the cards asked since he was last heard its only additions; a post is recorded on the one order cli.post_order decides, never one its caller names, wherever it goes, delivering the parts its report record names, making the clock time its words promise his next line by the order's due, and adding its thread to the order's threads. A report's post names each correction cci holds re it, which the post may lack, and a correction of a report held for his catch-up or carried by it posts nothing, naming where that report's copy is. A handler's result line is kept on its entry, for its done record to carry whole. What the drive's checks find in its words, the say hook hands Jev to judge against his words once this has posted them, before only a post a count or a mark refuses, such as one over its length or to him while he is away, a post saying a release freeze lifted or a release may start while a freeze or his release hold it does not name stands, and a dry run."""
    if (close := post.get("close")) and (post.get("record") or post.get("edit")):
        raise DriveError("nothing posted: close answers the order of what your handler holds and writes its report itself, so pass no record or edit")
    report = cli.report_attrs(post["record"]) if post.get("record") else {}
    seq = report.get("seq")
    known = state.load(name)
    order = cli.post_order(known, os.environ.get("CLAUDE_CODE_SESSION_ID"), post, report)
    if close and not order:
        raise DriveError("nothing posted: close answers the order of the message of his or the record your handler holds, and no order holds it")
    if close:
        report = {"close": order, "writer": "communicator", "written": state.stamp()}
    if refused := (order and cli.report_refusal(known, order, report)) or (close and cli.unfiled_refusal(known, order)) or held_refusal(known, seq, report):
        raise DriveError(f"nothing posted: {refused}")
    if refused := brief.claim_refusal(post.get("text") or "", known):
        raise DriveError(f"nothing posted: {refused}")
    if report.get("catch_up"):
        if known["catch_up"] is None:
            raise DriveError(f"nothing posted: report {seq} is a catch-up, and the drive owes him none, since its last catch-up already posted")
        post = post | {"text": titled(known, post.get("text") or "") + waiting(name, known, post.get("text") or "")}
    if post.get("dry_run"):
        return dry(name, post) + (f"; {cli.delivering(name, order, report)}" if order else "")
    held = list(known["held"]) if report.get("catch_up") else []
    amended = agents.amendments(name, [seq, *held] if seq else [])
    if close:
        report["seq"] = close_report(name, order, post.get("text") or "")
    claimed = cli.claim_close(name, report)
    try:
        reply = posted(name, post, order)
    except (DriveError, slack.SlackError):
        cli.unclaim_close(name, claimed)
        raise
    line = f"{'EDITED' if post.get('edit') else 'POSTED'} {reply['permalink']}"
    if card := reply.get("card_permalink"):
        line += f" CARD {card}"
    if handler := state.handler_id(known, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        with state.edit(name) as data:
            data["handlers"][handler]["posted"].append(line)
    if warning := reply.get("warning"):
        line += f" WARNING {warning}"
    if found := amended["correction"].get(seq):
        line += f"; cci holds corrections of report {seq}, {', '.join(found)}: unless this post carries each, reword it with say(edit={reply['permalink']}, text=…)"
    if report.get("catch_up"):
        line += released(name, reply["permalink"], amended)
    if order:
        line += "; " + recording(name, {"order": order, "link": reply["permalink"], "record": seq, "sent": reply.get("sent")}, report if close else None)
    if close:
        line += closed(name, order, report["seq"], reply["permalink"], post.get("text") or "")
    return line


def recording(name: str, receipt: dict, closing: dict | None = None) -> str:
    """Record the post on its order in its own process, behind a receipt kept in the drive, so a slow Slack read, Jev call or drive lock never holds the say call past RECORD_WAIT_SECONDS: the record's line once it lands within that, else where its receipt keeps the line it ends with. Whether this session may record a post on the order is read in the call, while the session is bound: the process runs as no session, and a handler's binding ends with the handler. A post that closes its order records in the call on the report its close writes, closing, so the order takes the post before it is set done."""
    try:
        cli.communicators_order(state.load(name), receipt["order"])
    except DriveError as error:
        return unkept(name, receipt, error, "")
    if closing is not None:
        return recorded(name, receipt, closing, "")
    kept = state.drive_dir(name) / "posts" / f"{uuid.uuid4().hex}.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(receipt))
    state.detach(name, ["say-record", str(kept)], "posts.log")
    ends = time.monotonic() + RECORD_WAIT_SECONDS
    while time.monotonic() < ends:
        if line := json.loads(kept.read_text()).get("line"):
            return line
        time.sleep(RECORD_POLL_SECONDS)
    return f"recording on {receipt['order']} after this call; its line lands in {kept}, and a failure reaches the meta session as a defect"


def unrecorded(name: str, text: str) -> None:
    """Tell the meta session, as a defect, that a post say made was not recorded on its order."""
    from drive import tools

    tools.sent(name, "communicator", [{"kind": "defect", "to": [f"meta@{name}"], "text": text}])


def record_kept(name: str, kept: Path) -> str:
    """Record a say post on its order from the receipt `recording` kept, writing the line it ends with back into the receipt; one that cannot be recorded is sent to the meta session as a defect naming the receipt."""
    receipt = json.loads(kept.read_text())
    line = recorded(name, receipt, None, f"; receipt {kept}")
    tmp = kept.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt | {"line": line}))
    os.replace(tmp, kept)
    return line


def recorded(name: str, receipt: dict, report: dict | None, where: str) -> str:
    """Record a say post on its order on its report, read from cci by the receipt's record unless given; one that cannot be recorded is sent to the meta session as a defect, `where` naming the receipt when one was kept."""
    try:
        report = report if report is not None else cli.report_attrs(receipt["record"]) if receipt["record"] else {}
        return cli.record_post(name, receipt["order"], receipt["link"], report, receipt["sent"])[0]
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


def hold(name: str, seq: str, report: dict, order_id: str | None) -> str:
    """Mark a report whose line waits for his catch-up held on the drive, once, answering it with a refused record to the coordinator or meta session that wrote it, else the coordinator session hearing its order, naming each correction cci already holds re it; that record's seq."""
    data = state.load(name)
    if mark := data["held"].get(seq):
        return mark["told"]
    writers = ["root", "meta", *map(state.project_lane, data["subcoordinators"])]
    lane = report["writer"] if report["writer"] in writers else state.heard_lane(data, order_id) if order_id else "root"
    argv = ["cci", "post", "--drive", name, "--lane", "communicator", "--kind", "refused", "--re", seq, "--to", f"{lane}@{name}", "--id", f"held-{seq}"]
    argv += ["--attr", f"held={seq}", *(["--attr", f"order={order_id}"] if order_id else [])]
    corrected = agents.amendments(name, [seq])["correction"].get(seq, [])
    text = HELD.format(seq=seq) + (f"; the catch-up carries it{render.corrected_by(corrected)}" if corrected else "")
    try:
        told = str(json.loads(reads.run([*argv, "--json", "--text", text], Path.cwd(), reads.posting()))["seq"])
    except reads.ReadError as error:
        raise DriveError(f"report {seq} could not be marked held: {error}") from None
    with state.edit(name) as data:
        data["held"][seq] = {"order": order_id, "at": state.stamp(), "told": told}
    return told


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
    """Why a report's or a correction's post goes nowhere: the report is held for his catch-up, as the away check marks it whatever Jev then reads, or the report a correction corrects is, since the catch-up carries the correction with it, or went out in his catch-up, whose post the correction rewords."""
    if seq in data["held"]:
        return f"report {seq} is held for his catch-up, which carries it: return NOTHING held for his catch-up"
    corrected = report.get("corrects")
    if corrected in data["held"]:
        return f"record {seq} corrects report {corrected}, held for his catch-up, which carries this correction with it: return NOTHING held for his catch-up"
    if post := data["carried"].get(corrected):
        return f"record {seq} corrects report {corrected}, which his catch-up {post} carried: reword that post with say(edit={post}, text=…)"
    return None


def titled(data: dict, text: str) -> str:
    """His catch-up, opening with the title the tool writes from the time he was last heard before the absence it covers, in place of any title the text came with."""
    return f"*Catch-up since {render.when(data['catch_up']['since'])}*\n{CATCH_UP_TITLE.sub('', text, count=1)}"


def waiting(name: str, data: dict, text: str) -> str:
    """The lines his catch-up closes with: a count of every open card of his the status page queues as waiting on him, linking that queue on the dashboard, then each one asked since he was last heard that the text does not already name, in the queue's order, on its own line, with a blank line after each five, where cc-slack starts its next message since Slack draws five a message. A card asked before he went away stays behind the count, by his words on a catch-up that drew every card: "including all tehse cards each time ^^ is not helpful and is very obtrusive". The say hook counts only the text it was given, so no word limit counts these."""
    try:
        found = view.cards(name)
    except view.CardsError as error:
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
    """Refuse an edit carrying anything but new words for its post, or a fence that draws as a picture, which an edit of a Slack message cannot add, a PR card naming a PR the drive holds none of, which it cannot draw, a post outside his DM naming his DM, carrying about, which names a card his reply in his DM is about, or with no thread in a channel, where only a person's user id opens a conversation, the group DM of that person, him and the bot, and a card there, a thread outside his DM named with no channel_id, and a PR card leaving out the thread outside his DM of an order its PRs carry out, which its line goes in."""
    data = state.load(name)
    if channel := post.get("channel_id"):
        if channel == slack.dm():
            raise DriveError(f"nothing posted: channel_id {channel} is his DM; leave channel_id out, and say posts there through cc-slack dm-status")
        if not post.get("thread_ts") and not cli.SLACK_USER.fullmatch(channel):
            raise DriveError(f"nothing posted: channel_id {channel} names the channel of a thread outside his DM, so it takes that thread's thread_ts; a person's user id with no thread posts in the group DM of that person, him and the bot")
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
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    if strays := [key for key in stack if key not in data["prs"]]:
        raise DriveError(f"tap_prs names {', '.join(strays)}, which the drive holds no PR for, so nothing was posted: drive pr add records it")
    outside = [where for where in state.pr_threads(data, stack) if where[0] != slack.dm()]
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
    dm = slack.dm()
    place = next((where[0] for order in data["orders"].values() for where in [state.promise_thread(order), *order["threads"]] if where and where[1] == thread and where[0] != dm), None)
    return place and f"nothing posted: thread {thread} is in channel {place}, not his DM, so pass channel_id {place} with it"


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


def posted(name: str, post: dict, order: str | None = None) -> dict:
    """cc-slack's reply to the post, or to the edit rewording a post of its own, which went out with its references linked, each visual drawn as a picture but a chart, which Slack draws itself, and each code or diagram fence of its text drawn as a picture too, and is kept as a said event for the dashboard, which draws each visual and fence live from its source and each PR of a stack card with its state now, an edit's words in place of its post's; a post that draws a card stores his cards as cc-slack lists them now, so the next post's hook sees that card; a post check refuses goes nowhere, and a new post of words alone that cc-slack reports failing is read back from Slack, kept and recorded when it went out anyway, cc-slack's error its warning. A card's line goes in each thread of his DM its PRs' orders have, and through cc-slack reply in the thread outside his DM the post names, such as an alert's, so whichever he reads carries the card's link, and the thread of each PR card drawn joins every order its PRs serve; the post's permalink is then that line's."""
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
    began = time.time()
    try:
        reply = slack.cc_slack(
            *(
                ["edit", "--url", post["edit"], "--text", sent, *ticketed(post)]
                if post.get("edit")
                else replied(post, pictures, sent)
                if first[0] != dm
                else status(name, post | {"thread_ts": first[1]}, stack, pictures, sent)
            )
        )
    except slack.SlackError as error:
        if post.get("edit") or drawn or not sent:
            raise
        reply = landed(name, post, began, sent, error)
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
    )
    if drawn:
        view.refresh_cards(name)
    else:
        reply |= {"sent": sent}
    cards = [reply.get("card_permalink"), *(linked(name, reply["permalink"], place[1], stack, refs.slack(words, found, place[1]), kept) for place in rest if place[0] == dm)]
    if stack:
        with state.edit(name) as data:
            state.join_pr_cards(data, stack, cards, state.stamp())
    label = card["title"] if card else "; ".join(view.pr_title(data["prs"][key]) for key in stack)
    for place in [place for place in rest if place[0] != dm]:
        reply |= {"permalink": carried(name, reply, place, post, refs.slack(words, found, place[1]), label, kept, stack)}
    return reply


def landed(name: str, post: dict, began: float, sent: str, error: slack.SlackError) -> dict:
    """cc-slack's reply, read back from Slack, for a new post of words alone it reported failing: the bot's message sent since the call began, in the post's thread or at its channel's root, opening with the post's words, since cc-slack can post and then fail, as dm-status does when its deadline passes after the post went out; the error again when Slack holds no such message."""
    channel = post.get("channel_id") or slack.dm()
    if thread := post.get("thread_ts"):
        read = slack.cc_slack("thread", "--channel", channel, "--thread", thread, "--since-ts", str(int(began)), "--limit", str(RECENT_POSTS))
        team_url = view.stored_cards(name)[0]["team_url"]
        messages = [
            message | {"permalink": f"{team_url}archives/{read['channel_id']}/p{message['ts'].replace('.', '')}?thread_ts={thread}&cid={read['channel_id']}"}
            for message in read["messages"]
        ]
    else:
        messages = [message | {"thread_ts": message["ts"]} for message in slack.cc_slack("history", "--channel", channel, "--limit", str(RECENT_POSTS), "--json")["messages"]]
    opening = WORD.findall(sent)[:OPENING_WORDS]
    for message in messages:
        if message.get("from_claude") and float(message["ts"]) >= int(began) and WORD.findall(html.unescape(message["text"]))[: len(opening)] == opening:
            return {"permalink": message["permalink"], "thread_ts": message.get("thread_ts"), "warning": f"cc-slack said {error}, but the post is in Slack"}
    raise error


def places(data: dict, post: dict, stack: list[str]) -> list[tuple[str, str | None]]:
    """Each channel and thread the post goes in, (his DM, None) for his DM root and (a person's user id, None) for the group DM of that person, him and the bot: the one it names; for a card, which cc-slack dm-status draws in his DM alone, first each place in his DM, the one it names, then for a PR card each thread of the orders its PRs carry out, else his DM root, then the one outside his DM it names, such as an alert's thread, which takes the card's line, since cc-slack's Slack permission hook judges a say for the place it names alone."""
    dm = slack.dm()
    named = place(post)
    if not (post.get("card") or stack):
        return [named]
    linked = [where for where in state.pr_threads(data, stack) if where[0] == dm or where == named]
    spots = list(dict.fromkeys([named, *linked] if named != (dm, None) else linked))
    return [*([where for where in spots if where[0] == dm] or [(dm, None)]), *(where for where in spots if where[0] != dm)]


def place(post: dict) -> tuple[str, str | None]:
    """The channel and thread a say post is in, None at the channel's root: an edit's are those of the message it rewords, read from its permalink, which an edit naming no Slack message refuses, and a new post's the ones it names, his DM for none."""
    if not post.get("edit"):
        return post.get("channel_id") or slack.dm(), post.get("thread_ts")
    if (message := state.slack_message(post["edit"])) is None:
        raise DriveError(f"nothing edited: edit {post['edit']} is no Slack permalink; pass the link the post's POSTED line gave")
    channel, ts, thread = message
    return channel, None if thread == ts else thread


def shown_at(place: tuple[str, str | None]) -> str:
    if place[0] == slack.dm():
        return f"thread {place[1]}" if place[1] else "his DM root"
    return f"thread {place[1]} of channel {place[0]}" if place[1] else f"the group DM of {place[0]}, him and the bot"


def shown_in(name: str, post: dict) -> str | None:
    """The thread a post goes in first, the thread of the card its `about` names, where cc-slack puts that reply, None for his DM root: a card named in its own thread reads there as words."""
    if post.get("about"):
        return view.card_thread(refs.card(refs.drawn_cards(name), state.load(name), post["about"])["url"])
    return places(state.load(name), post, [state.pr_key(each) for each in post.get("tap_prs") or []])[0][1]


def linked(name: str, first: str, thread: str, stack: list[str], sent: str, kept: str) -> str | None:
    """Post a PR card's line in one more thread of his DM, where cc-slack links the card it drew under the line, and keep it for the dashboard; the card's link cc-slack names. A failure says the card already went out, so nothing is posted again."""
    try:
        reply = slack.cc_slack(*status(name, {"thread_ts": thread}, stack, [], sent))
    except slack.SlackError as error:
        raise slack.SlackError(f"posted {first} with its card, but its line in thread {thread} did not go out, so post neither again: {error}") from None
    state.append_event(name, "said", text=kept, thread=reply.get("thread_ts"), url=reply["permalink"], card=reply.get("card_permalink"), order=None, visuals=[], prs=stack, edited=False)
    return reply.get("card_permalink")


def carried(name: str, reply: dict, place: tuple[str, str], post: dict, sent: str, label: str, kept: str, stack: list[str]) -> str:
    """Post a card's line in a thread outside his DM, such as an alert's, linking the card cc-slack drew in his DM, since cc-slack dm-status links a card into his DM's threads alone, through cc-slack reply with the ticket cc-slack's Slack permission hook attached to the say call, and keep it for the dashboard; its permalink. A failure says the card already went out, so nothing is posted again."""
    card = (reply.get("card_permalink") or reply["permalink"]).split()[0]
    text = "\n".join(filter(None, [sent, f"<{card}|{label.translate(refs.LINK_WORDS)}>"]))
    try:
        line = slack.cc_slack("reply", "--agent", "claude", "--channel", place[0], "--thread", place[1], "--text", text, *ticketed(post))
    except slack.SlackError as error:
        raise slack.SlackError(f"posted {card}, but its line in thread {place[1]} of channel {place[0]} did not go out, so post neither again: {error}") from None
    state.append_event(name, "said", text=kept, channel=place[0], thread=line.get("thread_ts"), url=line["permalink"], card=card, order=None, visuals=[], prs=stack, edited=False)
    return line["permalink"]


def status(name: str, post: dict, stack: list[str], pictures: list[str], sent: str) -> list[str]:
    """The `cc-slack dm-status` call making a new post in his DM."""
    prs = state.load(name)["prs"]
    argv = ["dm-status", "--agent", "claude", "--drive", name]
    if thread := post.get("thread_ts"):
        argv += ["--thread", thread]
    if card := post.get("card"):
        argv += ["--card", json.dumps(card)]
    if about := post.get("about"):
        argv += ["--about", about]
    argv += [flag for key in stack for flag in ("--tap-pr", prs[key]["url"])]
    argv += [flag for path in pictures for flag in ("--file", path)]
    return [*argv, "--", sent] if sent else argv


def draw(name: str, keys: list[str], again: bool = False) -> str:
    """Post his card on the PRs at his DM root and in no order's thread, under the line `waits_line` writes from their records, as drive pr add draws it on the row it makes and the watch on a PR still uncarded, or `again` on PRs that wait on him while they fix a live bug, keep it as a said event, and store his cards as cc-slack lists them now, which view.tap_card reads; its link. A card drive card refuses for every one of them, or a line `plain` refuses, posts nothing and says why."""
    from drive import card

    drawn = card.cards([{"repo": key.split("#")[0], "number": int(key.split("#")[1])} for key in keys])
    if not drawn["cards"]:
        raise DriveError("; ".join(f"{skip['repo']}#{skip['number']}: {skip['reason']}" for skip in drawn["skipped"]) or f"drive card drew no card on {', '.join(keys)}")
    line = waits_line(state.load(name), keys, [row["title"] for each in drawn["cards"] for row in each["card"]["prs"]], again)
    plain(line)
    reply = slack.cc_slack(*status(name, {}, keys, [], line))
    link = (reply.get("card_permalink") or reply["permalink"]).split()[0]
    state.append_event(
        name, "said", text=line, channel=None, thread=reply.get("thread_ts"), url=reply["permalink"], card=reply.get("card_permalink"), order=None, visuals=[], prs=keys, edited=False
    )
    view.refresh_cards(name)
    return link


def waits_line(data: dict, keys: list[str], titles: list[str], again: bool) -> str:
    """The line over the drive's own draw of his card, with no link, so Slack draws no other card: what the PRs on it do, by their titles, under "Waits on your Approve", or asked again, why it asks, the open incident or reported bug they fix; a flake fix adds that it fixes a flaking test at its cause, by his ruling FLAKE_RULING, its words quoted."""
    opening = "Waits on your Approve"
    if again:
        incident = any(data["orders"][order_id]["incident"] for key in keys for order_id in state.served_orders(data, data["prs"][key]))
        opening = f"Asking again, since it fixes {'an open incident' if incident else 'a reported bug'}"
    flaky = any(data["prs"][key].get("exempt") == state.FLAKE_FIX for key in keys)
    return "\n".join([f"{opening}: {'; '.join(titles)}", *([f'Fixes a flaking test at its cause, by your ruling "{state.FLAKE_WORDS}"'] if flaky else [])])


def plain(line: str) -> None:
    """Refuse a line over his card that `cc-slack check-post` finds something in and Jev, in one call, reads as breaking the plain-words rule; a line Jev gives no reading of goes out, said on stderr, as a say post does when its judge cannot finish."""
    from drive import settled

    if not (findings := slack.post_findings(line, True)):
        return
    question = {"label": {"instructions": PLAIN_QUESTION.format(rule=agents.PLAIN_WORDS_RULE), "options": {settled.UNDECIDED: "No finding's rule holds against the line.", BREAKS: "A finding's rule holds against the line."}}}
    try:
        read = settled.jev({"state": {"line": line, "findings": findings}, "questions": {BREAKS: question}})[BREAKS]
    except settled.Unheard as failure:
        print(f"drive: Jev gave no reading of the line over his card, so it goes out with what check-post found in it, {'; '.join(findings)}: {failure}", file=sys.stderr)
        return
    if read["choice"] == BREAKS and read["probabilities"][BREAKS] >= settled.DECIDES:
        raise DriveError(f"nothing was posted: the line over his card breaks the plain-words rule, as `cc-slack check-post` finds and Jev reads it: {'; '.join(findings)}. Line: {line}")


def withdraw(name: str, key: str) -> list[str]:
    """Withdraw each open card of his carrying a PR reworked to his words, his words its reason, so no card asks his tap on it until its builder's READY says how they are met and the watch draws a fresh one; each card withdrawn."""
    pr = state.load(name)["prs"][key]
    withdrawn = slack.withdraw_pr_card(pr["url"], state.rework_reason(pr))
    view.refresh_cards(name)
    return withdrawn


def replied(post: dict, pictures: list[str], sent: str) -> list[str]:
    """The cc-slack call making a new post outside his DM, with the ticket cc-slack's Slack permission hook attached to the say call: `cc-slack reply` in a thread, or with no thread `cc-slack send` to a person's user id, which opens the group DM of that person, him and the bot."""
    thread = ["--thread", post["thread_ts"]] if post.get("thread_ts") else []
    argv = ["reply" if thread else "send", "--agent", "claude", "--channel", post["channel_id"], *thread, "--text", sent]
    argv += [flag for path in pictures for flag in ("--file", path)]
    return argv + ticketed(post)


def ticketed(post: dict) -> list[str]:
    """The flags handing cc-slack the ticket its Slack permission hook attached to the say call, which the daemon spends on a write outside his DM and on an edit."""
    return [each for field, flag in TICKET_FLAGS.items() if post.get(field) for each in (flag, post[field])]


def unquoted_tooling(data: dict, stack: list[str], text: str) -> str | None:
    """Why a PR card may not go out: a PR on it serves a drive tooling order whose own words the line above the card leaves out."""
    for key in stack:
        order = data["orders"].get(state.order_served(data["prs"][key]))
        if order and order["text"].startswith(state.TOOLING) and order["text"] not in text:
            return f'{key} serves a drive tooling order, so the line above its card quotes that order: put "{order["text"]}" in text'
    return None


def kind(visual: dict) -> str:
    return f"code:{visual.get('language') or ''}" if visual["kind"] == "code" else visual["kind"]


def drawn(name: str, which: str, visual: dict) -> Path:
    """The picture Slack shows for one visual, named after its caption, which Slack titles it with: code highlighted, a diagram or compare drawn as the dashboard draws it, an image as it is; one that does not draw posts nothing."""
    return picture.shot(
        state.drive_dir(name) / "pictures", picture.file_name(visual.get("caption") or "") or "show-me", kind(visual), visual["source"], f"nothing was posted: {which} ({visual['kind']})"
    )
