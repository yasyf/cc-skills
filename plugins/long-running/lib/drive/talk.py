import json
import re
from pathlib import Path

from drive import cli, picture, refs, render, slack, state, view
from drive.state import DriveError

WEB = ("http://", "https://")
UNNAMEABLE = re.compile(r'[\x00-\x1f/\\:*?"<>|\[\]]+')
CATCH_UP_TITLE = re.compile(r"\A\s*\**\s*catch-up since\b[^\n]*\n*", re.IGNORECASE)


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack and keep the same item on the drive's dashboard, opening with each card of his that holds built work in its thread, after a catch-up's title; a post in an order's thread is recorded on that order too, delivering the parts its report record names."""
    report = cli.report_attrs(post["record"]) if post.get("record") else {}
    if post.get("order") and (refused := cli.report_refusal(state.load(name), post["order"], report)):
        raise DriveError(f"nothing posted: {refused}")
    if report.get("catch_up"):
        post = post | {"text": titled(name, post.get("text") or "")}
    post = post | {"text": waiting_first(name, post)}
    if post.get("dry_run"):
        return dry(name, post)
    reply = posted(name, post)
    line = f"POSTED {reply['permalink']}"
    if card := reply.get("card_permalink"):
        line += f" CARD {card}"
    if warning := reply.get("warning"):
        line += f" WARNING {warning}"
    if order := post.get("order"):
        try:
            line += "; " + cli.record_post(name, order, reply["permalink"], report)[0]
        except (DriveError, slack.SlackError) as error:
            line += f"; not recorded on {order}: {error}"
    return line


def titled(name: str, text: str) -> str:
    """His catch-up, opening with the title the tool writes from the time he was last heard before he came back, in place of any title the text came with."""
    [*_, back] = [event for event, _ in state.read_events(name) if event["kind"] == "catch-up"]
    return f"*Catch-up since {back['since']}*\n{CATCH_UP_TITLE.sub('', text, count=1)}"


def waiting_first(name: str, post: dict) -> str:
    """The post's text, opened, after any catch-up title, by a WAITING-ON-HIM line for each card of his that holds a built job of an order in its thread, or of any order at his DM root, that it does not carry yet; a card or PR card opens with none."""
    text = post.get("text") or ""
    found = view.live_cards(name)
    if post.get("card") or post.get("tap_prs") or found is None:
        return text
    data = state.load(name)
    thread = post.get("thread_ts")
    lines = [
        line
        for entry in view.waiting_on_him(data, found)
        if (not thread or view.dm_thread(data, data["orders"][entry["order"]]) == thread) and (line := render.waiting_line(entry)) not in text
    ]
    title = CATCH_UP_TITLE.match(text)
    cut = title.end() if title else 0
    return "\n".join(filter(None, [text[:cut].rstrip("\n"), *lines, text[cut:]]))


def dry(name: str, post: dict) -> str:
    """What say would post, its references resolved and its pictures drawn, with nothing posted."""
    text = post.get("text") or ""
    visuals, words, lifted = shown(name, post)
    where = f"thread {post['thread_ts']}" if post.get("thread_ts") else "his DM root"
    card = f", card {post['card'].get('title')!r}" if post.get("card") else ""
    prs = f", PR card for {len(post['tap_prs'])} PRs" if post.get("tap_prs") else ""
    pictures = len([visual for visual in visuals if "picture" in visual]) + len(lifted)
    return f"DRY {where}, {pictures} pictures{card}{prs}: {refs.slack(words, refs.resolved(name, text))}"


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
    """cc-slack's reply to the post, which went out with its references linked, each visual drawn as a picture but a chart, which Slack draws itself, and each code or diagram fence of its text drawn as a picture too, and is kept as a said event for the dashboard, which draws each visual and fence live from its source and each PR of a stack card with its state now."""
    text = post.get("text") or ""
    found = refs.resolved(name, text)
    visuals, words, lifted = shown(name, post)
    prs = state.load(name)["prs"]
    stack = [state.pr_key(each) for each in post.get("tap_prs") or []]
    if strays := [key for key in stack if key not in prs]:
        raise DriveError(f"tap_prs names {', '.join(strays)}, which the drive holds no PR for, so nothing was posted: drive pr add records it")
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
    reply = slack.cc_slack(*argv)
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


def kind(visual: dict) -> str:
    return f"code:{visual.get('language') or ''}" if visual["kind"] == "code" else visual["kind"]


def drawn(name: str, which: str, visual: dict) -> Path:
    """The picture Slack shows for one visual, named after its caption, which Slack titles it with: code highlighted, a diagram or compare drawn as the dashboard draws it, an image as it is."""
    source = visual["source"]
    if visual["kind"] == "image" and not source.startswith(WEB):
        if not (local := Path(source).expanduser()).is_absolute() or not local.is_file():
            raise DriveError(f"{which}: {source} is neither a link nor a file here")
        source = local.as_uri()
    if visual["kind"] == "code" and (what := picture.raw(visual.get("language") or "", source)):
        raise DriveError(f"{which} is {what}, raw data or tool output, so nothing was posted; {picture.INSTEAD}")
    try:
        return picture.cached(state.drive_dir(name) / "pictures", " ".join(UNNAMEABLE.sub(" ", visual.get("caption") or "").split()) or "show-me", kind(visual), source)
    except picture.PictureError as error:
        raise DriveError(f"{which} ({visual['kind']}) does not draw, so nothing was posted: {error}") from None
