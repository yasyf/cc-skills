import json
import re
from pathlib import Path

from drive import cli, picture, reads, refs, slack, state
from drive.state import DriveError

WEB = ("http://", "https://")
CATCH_UP_TITLE = re.compile(r"\A\s*\**\s*catch-up since\b[^\n]*\n*", re.IGNORECASE)
HELD = "report {seq} held for his catch-up, since he is away; drive status lists it until the catch-up posts"


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack and keep the same item on the drive's dashboard, in the words it was given, a catch-up's title its only addition; a post in an order's thread is recorded on that order too, delivering the parts its report record names, and one on an order in none of its threads posts nothing. What the drive's checks find in its words, the say hook hands Jev to judge against his words before this runs."""
    report = cli.report_attrs(post["record"]) if post.get("record") else {}
    if order := post.get("order"):
        data = state.load(name)
        if refused := cli.report_refusal(data, order, report) or cli.thread_refusal(data, order, post.get("thread_ts")):
            raise DriveError(f"nothing posted: {refused}")
    if report.get("catch_up"):
        post = post | {"text": titled(name, post.get("text") or "")}
    if post.get("dry_run"):
        return dry(name, post)
    reply = posted(name, post)
    line = f"POSTED {reply['permalink']}"
    if card := reply.get("card_permalink"):
        line += f" CARD {card}"
    if warning := reply.get("warning"):
        line += f" WARNING {warning}"
    if report.get("catch_up"):
        line += released(name)
    if order:
        try:
            line += "; " + cli.record_post(name, order, reply["permalink"], report)[0]
        except (DriveError, slack.SlackError) as error:
            line += f"; not recorded on {order}: {error}"
    return line


def hold(name: str, seq: str, report: dict, order_id: str | None) -> str:
    """Mark a report whose line waits for his catch-up held on the drive, once, answering it with a refused record to the coordinator session that wrote it, else the one hearing its order; that record's seq."""
    data = state.load(name)
    if mark := data["held"].get(seq):
        return mark["told"]
    coordinators = ["root", *map(state.project_lane, data["subcoordinators"])]
    lane = report["writer"] if report["writer"] in coordinators else state.heard_lane(data, order_id) if order_id else "root"
    argv = ["cci", "post", "--drive", name, "--lane", "communicator", "--kind", "refused", "--re", seq, "--to", f"{lane}@{name}", "--id", f"held-{seq}"]
    argv += ["--attr", f"held={seq}", *(["--attr", f"order={order_id}"] if order_id else [])]
    try:
        told = str(json.loads(reads.run([*argv, "--json", "--text", HELD.format(seq=seq)], Path.cwd()))["seq"])
    except reads.ReadError as error:
        raise DriveError(f"report {seq} could not be marked held: {error}") from None
    with state.edit(name) as data:
        data["held"][seq] = {"order": order_id, "at": state.stamp(), "told": told}
    return told


def released(name: str) -> str:
    """Let go of every report held for his catch-up, which the catch-up just posted covers, and name them."""
    with state.edit(name) as data:
        held, data["held"] = data["held"], {}
    return f"; it carries the reports held for it, no longer held: {', '.join(held)}" if held else ""


def titled(name: str, text: str) -> str:
    """His catch-up, opening with the title the tool writes from the time he was last heard before he came back, in place of any title the text came with."""
    [*_, back] = [event for event, _ in state.read_events(name) if event["kind"] == "catch-up"]
    return f"*Catch-up since {back['since']}*\n{CATCH_UP_TITLE.sub('', text, count=1)}"


def dry(name: str, post: dict) -> str:
    """What say would post, its references resolved and its pictures drawn, with nothing posted, refused as the post itself would be; the say hook judges its words as it judges the post's."""
    found = refs.resolved(name, post.get("text") or "")
    check(name, post)
    visuals, words, lifted = shown(name, post)
    where = f"thread {post['thread_ts']}" if post.get("thread_ts") else "his DM root"
    card = f", card {post['card'].get('title')!r}" if post.get("card") else ""
    prs = f", PR card for {len(post['tap_prs'])} PRs" if post.get("tap_prs") else ""
    pictures = len([visual for visual in visuals if "picture" in visual]) + len(lifted)
    return f"DRY {where}, {pictures} pictures{card}{prs}: {refs.slack(words, found)}"


def check(name: str, post: dict) -> None:
    """Refuse a PR card naming a PR the drive holds none of, which it cannot draw."""
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
    """cc-slack's reply to the post, which went out with its references linked, each visual drawn as a picture but a chart, which Slack draws itself, and each code or diagram fence of its text drawn as a picture too, and is kept as a said event for the dashboard, which draws each visual and fence live from its source and each PR of a stack card with its state now; a post check refuses goes nowhere."""
    text = post.get("text") or ""
    found = refs.resolved(name, text)
    check(name, post)
    visuals, words, lifted = shown(name, post)
    prs = state.load(name)["prs"]
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    argv = ["dm-status", "--agent", "claude"]
    if thread := post.get("thread_ts"):
        argv += ["--thread", thread]
    if card := post.get("card"):
        argv += ["--card", json.dumps(card)]
    if about := post.get("about"):
        argv += ["--about", about]
    argv += [flag for key in stack for flag in ("--tap-pr", prs[key]["url"])]
    argv += [flag for path in [*(visual["picture"] for visual in visuals if "picture" in visual), *lifted] for flag in ("--file", path)]
    if words:
        argv += ["--", refs.slack(words, found)]
    asked = cli.asked_on(name, card["waits"]) if card and card.get("waits") else set()
    reply = slack.cc_slack(*argv)
    if card:
        reply |= {"card_permalink": " ".join([reply["permalink"], *filter(None, [reply.get("card_permalink")])])}
    if card and card.get("waits"):
        cli.follow_card(name, card["waits"], asked, reply["permalink"])
    state.append_event(
        name,
        "said",
        text=refs.board(text, found) or (card or {}).get("title") or "",
        thread=reply.get("thread_ts"),
        url=reply["permalink"],
        card=reply.get("card_permalink"),
        order=post.get("order"),
        visuals=visuals,
        prs=stack,
    )
    return reply


def unquoted_tooling(data: dict, stack: list[str], text: str) -> str | None:
    """Why a PR card may not go out: a PR on it serves a drive tooling order whose own words the line above the card leaves out."""
    for key in stack:
        order = data["orders"].get(data["prs"][key].get("serves"))
        if order and order["text"].startswith(state.TOOLING) and order["text"] not in text:
            return f'{key} serves a drive tooling order, so the line above its card quotes that order: put "{order["text"]}" in text'
    return None


def kind(visual: dict) -> str:
    return f"code:{visual.get('language') or ''}" if visual["kind"] == "code" else visual["kind"]


def drawn(name: str, which: str, visual: dict) -> Path:
    """The picture Slack shows for one visual, named after its caption, which Slack titles it with: code highlighted, a diagram or compare drawn as the dashboard draws it, an image as it is."""
    source = visual["source"]
    if visual["kind"] == "image" and not source.startswith(WEB):
        if not (local := Path(source).expanduser()).is_absolute() or not local.is_file():
            raise DriveError(f"{which}: {source} is neither a link nor a file here")
        source = local.as_uri()
    try:
        return picture.cached(state.drive_dir(name) / "pictures", picture.file_name(visual.get("caption") or "") or "show-me", kind(visual), source)
    except picture.PictureError as error:
        raise DriveError(f"{which} ({visual['kind']}) does not draw, so nothing was posted: {error}") from None
