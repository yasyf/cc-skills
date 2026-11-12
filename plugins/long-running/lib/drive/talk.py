import json
import os
import re
from pathlib import Path

from drive import agents, cli, picture, reads, refs, render, slack, state, view
from drive.state import DriveError

CATCH_UP_TITLE = re.compile(r"\A\s*\**\s*catch-up since\b[^\n]*\n*", re.IGNORECASE)
CARDS_A_MESSAGE = 5
CARD_KINDS = (("question", "question"), ("tap", "PR card"))
HELD = "report {seq} held for his catch-up, since he is away; drive status lists it until the catch-up posts"
TICKET_FLAGS = {"ticket_id": "--ticket", "ticket_tree": "--ticket-tree", "ticket_agent": "--ticket-agent", "ticket_call": "--ticket-call", "ticket_fingerprint": "--ticket-fingerprint"}
EDIT_TAKES = {"edit", "text", "asked", "dry_run", *TICKET_FLAGS}
DM_ONLY = ("card", "tap_prs", "about")


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack, or in a thread outside it named by channel_id, such as an alert's, or with a person's user id as channel_id and no thread in the group DM of that person, him and the bot, or reword one it made in any of those, and keep the same item on the drive's dashboard, in the words it was given, a catch-up's title and the open cards it closes with its only additions; a post in an order's thread is recorded on that order too, delivering the parts its report record names and making the clock time its words promise his next line by the order's due, and one on an order in none of its threads posts nothing; the thread of the message of his that a report about the order answers, followed up the report's re, is one of them, and the post adds it to the order's threads. A report's post names each correction cci holds re it, which the post may lack, and a correction of a report held for his catch-up or carried by it posts nothing, naming where that report's copy is. A handler's result line is kept on its entry, for its done record to carry whole. What the drive's checks find in its words, the say hook hands Jev to judge against his words before this runs."""
    report = cli.report_attrs(post["record"]) if post.get("record") else {}
    if order := post.get("order"):
        data = state.load(name)
        where = (post.get("channel_id") or data["slack"]["channel"], post.get("thread_ts"))
        if refused := cli.report_refusal(data, order, report) or cli.thread_refusal(data, order, where, report):
            raise DriveError(f"nothing posted: {refused}")
    known = state.load(name)
    if refused := correction_refusal(known, post.get("record"), report):
        raise DriveError(f"nothing posted: {refused}")
    if report.get("catch_up"):
        if known["catch_up"] is None:
            raise DriveError(f"nothing posted: report {post['record']} is a catch-up, and the drive owes him none, since its last catch-up already posted")
        post = post | {"text": titled(known, post.get("text") or "") + waiting(name, post.get("text") or "")}
    if post.get("dry_run"):
        return dry(name, post)
    held = list(known["held"]) if report.get("catch_up") else []
    corrected = agents.corrections(name, [post["record"], *held]) if post.get("record") else {}
    reply = posted(name, post)
    line = f"{'EDITED' if post.get('edit') else 'POSTED'} {reply['permalink']}"
    if card := reply.get("card_permalink"):
        line += f" CARD {card}"
    if handler := state.handler_id(known, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        with state.edit(name) as data:
            data["handlers"][handler]["posted"].append(line)
    if warning := reply.get("warning"):
        line += f" WARNING {warning}"
    if found := corrected.get(post.get("record")):
        line += f"; cci holds corrections of report {post['record']}, {', '.join(found)}: unless this post carries each, reword it with say(edit={reply['permalink']}, text=…)"
    if report.get("catch_up"):
        line += released(name, reply["permalink"], corrected)
    if order:
        try:
            line += "; " + cli.record_post(name, order, reply["permalink"], report)[0]
        except (DriveError, slack.SlackError) as error:
            line += f"; not recorded on {order}: {error}"
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
    corrected = agents.corrections(name, [seq]).get(seq, [])
    text = HELD.format(seq=seq) + (f"; the catch-up carries it{render.corrected_by(corrected)}" if corrected else "")
    try:
        told = str(json.loads(reads.run([*argv, "--json", "--text", text], Path.cwd()))["seq"])
    except reads.ReadError as error:
        raise DriveError(f"report {seq} could not be marked held: {error}") from None
    with state.edit(name) as data:
        data["held"][seq] = {"order": order_id, "at": state.stamp(), "told": told}
    return told


def released(name: str, post: str, corrected: dict[str, list[str]]) -> str:
    """Settle the catch-up the drive owed him, which just posted at post, as the last line each order it covers got, and let go of every report held for it, keeping post as the copy each went out in, and name them, with each correction cci holds re one, which the post must carry."""
    with state.edit(name) as data:
        held, data["held"] = data["held"], {}
        data["carried"] |= dict.fromkeys(held, post)
        for order_id in data["catch_up"]["orders"]:
            data["orders"][order_id]["last_line"] = state.stamp()
        data["catch_up"] = None
    if not held:
        return ""
    fixes = [fix for seq in held for fix in corrected.get(seq, [])]
    line = f"; it carries the reports held for it, no longer held: {', '.join(held)}"
    return line + (f"; cci holds corrections {', '.join(fixes)} of them: unless this post carries each, reword it with say(edit={post}, text=…)" if fixes else "")


def correction_refusal(data: dict, seq: str | None, report: dict) -> str | None:
    """Why a correction's post goes nowhere: the report it corrects is held for his catch-up, which carries the correction with it, or went out in his catch-up, whose post the correction rewords."""
    corrected = report.get("corrects")
    if corrected in data["held"]:
        return f"record {seq} corrects report {corrected}, held for his catch-up, which carries this correction with it: return NOTHING held for his catch-up"
    if post := data["carried"].get(corrected):
        return f"record {seq} corrects report {corrected}, which his catch-up {post} carried: reword that post with say(edit={post}, text=…)"
    return None


def titled(data: dict, text: str) -> str:
    """His catch-up, opening with the title the tool writes from the time he was last heard before the absence it covers, in place of any title the text came with."""
    return f"*Catch-up since {render.when(data['catch_up']['since'])}*\n{CATCH_UP_TITLE.sub('', text, count=1)}"


def waiting(name: str, text: str) -> str:
    """The lines his catch-up closes with: every open card of his the status page queues as waiting on him, in its order, that the text does not already name, each on its own line under a count of them, with a blank line after each five, where cc-slack starts its next message since Slack draws five a message. The say hook counts only the text it was given, so no word limit counts these."""
    try:
        found = view.cards(name)
    except view.CardsError as error:
        raise DriveError(f"nothing posted: his catch-up draws every open card of his, which cc-slack could not list: {error}") from None
    named = {match[2] for match in refs.REF.finditer(text) if match[1] == "card"}
    queued = [item for item in view.build(state.load(name), found, state.now())["queue"] if "card" in item and item["card"]["waits"] not in named]
    if not queued:
        return ""
    counts = [f"{count} {word}{'' if count == 1 else 's'}" for kind, word in CARD_KINDS if (count := sum(item["kind"] == kind for item in queued))]
    lines = [f"{{card:{item['card']['waits']}}}" for item in queued]
    messages = ["\n".join(lines[start : start + CARDS_A_MESSAGE]) for start in range(0, len(lines), CARDS_A_MESSAGE)]
    return f"\n\n*Your open cards: {', '.join(counts)}*\n" + "\n\n".join(messages)


def dry(name: str, post: dict) -> str:
    """What say would post, its references resolved and its pictures drawn, with nothing posted, refused as the post itself would be; the say hook judges its words as it judges the post's."""
    found = refs.resolved(name, post.get("text") or "")
    check(name, post)
    visuals, words, lifted = shown(name, post)
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    where = f"edit of {post['edit']}" if post.get("edit") else ", ".join(f"thread {thread}" if thread else "his DM root" for thread in threads(name, post, stack))
    where += f" of channel {post['channel_id']}" if post.get("channel_id") else ""
    if post.get("channel_id") and not post.get("thread_ts"):
        where = f"the group DM of {post['channel_id']}, him and the bot"
    card = f", card {post['card'].get('title')!r}" if post.get("card") else ""
    prs = f", PR card for {len(post['tap_prs'])} PRs" if post.get("tap_prs") else ""
    pictures = len([visual for visual in visuals if "picture" in visual]) + len(lifted)
    return f"DRY {where}, {pictures} pictures{card}{prs}: {refs.slack(words, found, shown_in(name, post))}"


def check(name: str, post: dict) -> None:
    """Refuse an edit carrying anything but new words for its post, or a fence that draws as a picture, which an edit of a Slack message cannot add, a PR card naming a PR the drive holds none of, which it cannot draw, and a post outside his DM naming his DM, carrying what only cc-slack dm-status draws, or with no thread in a channel, where only a person's user id opens a conversation, the group DM of that person, him and the bot."""
    if channel := post.get("channel_id"):
        if channel == state.load(name)["slack"]["channel"]:
            raise DriveError(f"nothing posted: channel_id {channel} is his DM; leave channel_id out, and say posts there through cc-slack dm-status")
        if not post.get("thread_ts") and not cli.SLACK_USER.fullmatch(channel):
            raise DriveError(f"nothing posted: channel_id {channel} names the channel of a thread outside his DM, so it takes that thread's thread_ts; a person's user id with no thread posts in the group DM of that person, him and the bot")
        if extra := [key for key in DM_ONLY if post.get(key)]:
            raise DriveError(f"nothing posted: {', '.join(extra)} draws only in his DM, through cc-slack dm-status, so a post with channel_id takes none")
    if post.get("edit") and (extra := sorted(key for key, value in post.items() if value and key not in EDIT_TAKES)):
        raise DriveError(
            f"nothing edited: an edit rewords the text of one post of yours, so it takes edit, text and asked alone, and this one also"
            f" passes {', '.join(extra)}. Redraw a card with drive job card, and post anything new as a new post"
        )
    if post.get("edit") and not post.get("text"):
        raise DriveError("nothing edited: an edit needs text, the post's new words")
    if post.get("edit") and any(picture.pictured(fence[1], fence[2]) for fence in picture.FENCE.finditer(post["text"])):
        raise DriveError("nothing edited: a code or diagram fence draws as a picture, which an edit of a Slack message cannot add; post it as a new post")
    data = state.load(name)
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    if strays := [key for key in stack if key not in data["prs"]]:
        raise DriveError(f"tap_prs names {', '.join(strays)}, which the drive holds no PR for, so nothing was posted: drive pr add records it")


def shown(name: str, post: dict) -> tuple[list[dict], str, list[str]]:
    """The post's visuals as the dashboard keeps them, each but a chart with the picture Slack gets of it; the text Slack gets, each chart visual added as its ```chart fence, which Slack draws as its own chart, and each fence of the text that draws as a picture lifted out, while the dashboard draws it in place; and the pictures of those lifted fences. A fence of words or of raw data stays in the text, which Slack shows as the code block it is."""
    visuals = [
        {"kind": kind(visual), "source": visual["source"], "caption": visual.get("caption") or ""}
        | ({} if visual["kind"] in picture.NATIVE else {"picture": str(drawn(name, f"visual {index}", visual))})
        for index, visual in enumerate(post.get("visuals") or [], start=1)
    ]
    lifted = []

    def lift(fence: re.Match[str]) -> str:
        if not picture.pictured(fence[1], fence[2]):
            return fence[0]
        language = fence[1].lower()
        visual = {"kind": language} if language in picture.DIAGRAMS else {"kind": "code", "language": language}
        lifted.append(str(drawn(name, f"its ```{fence[1]} fence", visual | {"source": fence[2], "caption": picture.title(kind(visual))})))
        return ""

    text = picture.FENCE.sub(lift, post.get("text") or "")
    charts = ["\n".join(filter(None, [visual["caption"], f"```{visual['kind']}", visual["source"].strip("\n"), "```"])) for visual in visuals if "picture" not in visual]
    return visuals, "\n\n".join(filter(None, [text, *charts])), lifted


def posted(name: str, post: dict) -> dict:
    """cc-slack's reply to the post, or to the edit rewording a post of its own, which went out with its references linked, each visual drawn as a picture but a chart, which Slack draws itself, and each code or diagram fence of its text drawn as a picture too, and is kept as a said event for the dashboard, which draws each visual and fence live from its source and each PR of a stack card with its state now, an edit's words in place of its post's; a post that draws a card stores his cards as cc-slack lists them now, so the next post's hook sees that card; a post check refuses goes nowhere. A PR card's line goes in each thread its PRs' orders have, so whichever he reads carries the card's link."""
    text = post.get("text") or ""
    found = refs.resolved(name, text)
    check(name, post)
    visuals, words, lifted = shown(name, post)
    card = post.get("card")
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    sent = refs.slack(words, found, shown_in(name, post))
    pictures = [*(visual["picture"] for visual in visuals if "picture" in visual), *lifted]
    asked = cli.asked_on(name, card["waits"]) if card and card.get("waits") else set()
    first, *others = threads(name, post, stack)
    reply = slack.cc_slack(
        *(
            ["edit", "--url", post["edit"], "--text", sent, *ticketed(post)]
            if post.get("edit")
            else replied(post, pictures, sent)
            if post.get("channel_id")
            else status(name, post | {"thread_ts": first}, stack, pictures, sent)
        )
    )
    if card:
        reply |= {"card_permalink": " ".join([reply["permalink"], *filter(None, [reply.get("card_permalink")])])}
    if card and card.get("waits"):
        cli.follow_card(name, card["waits"], asked, reply["permalink"])
    kept = refs.board(text, found) or (card or {}).get("title") or ""
    state.append_event(
        name,
        "said",
        text=kept,
        channel=post.get("channel_id"),
        thread=reply.get("thread_ts"),
        url=reply["permalink"],
        card=reply.get("card_permalink"),
        order=post.get("order"),
        visuals=visuals,
        prs=stack,
        edited=bool(post.get("edit")),
    )
    if card or stack:
        view.refresh_cards(name)
    for thread in others:
        linked(name, reply["permalink"], thread, stack, refs.slack(words, found, thread), kept)
    return reply


def threads(name: str, post: dict, stack: list[str]) -> list[str | None]:
    """Each thread of his DM the post goes in, None for his DM root: the one it names, then for a PR card each thread of the orders its PRs carry out."""
    return list(dict.fromkeys(filter(None, [post.get("thread_ts"), *state.pr_threads(state.load(name), stack)]))) or [None]


def shown_in(name: str, post: dict) -> str | None:
    """The thread a post goes in first, the thread of the card its `about` names, where cc-slack puts that reply, or the thread an edit's post is in, None for his DM root: a card named in its own thread reads there as words."""
    if post.get("edit"):
        return view.card_thread(post["edit"])
    if post.get("about"):
        return view.card_thread(refs.card(refs.open_cards(name), state.load(name), post["about"])["url"])
    return threads(name, post, [state.pr_key(each) for each in post.get("tap_prs") or []])[0]


def linked(name: str, first: str, thread: str, stack: list[str], sent: str, kept: str) -> None:
    """Post a PR card's line in one more thread of his DM, where cc-slack links the card it drew under the line, and keep it for the dashboard; a failure says the card already went out, so nothing is posted again."""
    try:
        reply = slack.cc_slack(*status(name, {"thread_ts": thread}, stack, [], sent))
    except slack.SlackError as error:
        raise slack.SlackError(f"posted {first} with its card, but its line in thread {thread} did not go out, so post neither again: {error}") from None
    state.append_event(name, "said", text=kept, thread=reply.get("thread_ts"), url=reply["permalink"], card=reply.get("card_permalink"), order=None, visuals=[], prs=stack, edited=False)


def status(name: str, post: dict, stack: list[str], pictures: list[str], sent: str) -> list[str]:
    """The `cc-slack dm-status` call making a new post in his DM."""
    prs = state.load(name)["prs"]
    argv = ["dm-status", "--agent", "claude"]
    if thread := post.get("thread_ts"):
        argv += ["--thread", thread]
    if card := post.get("card"):
        argv += ["--card", json.dumps(card)]
    if about := post.get("about"):
        argv += ["--about", about]
    argv += [flag for key in stack for flag in ("--tap-pr", prs[key]["url"])]
    argv += [flag for path in pictures for flag in ("--file", path)]
    return [*argv, "--", sent] if sent else argv


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
