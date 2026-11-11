import json
import re
from pathlib import Path

from drive import cli, picture, refs, slack, state
from drive.state import DriveError

WEB = ("http://", "https://")
UNNAMEABLE = re.compile(r'[\x00-\x1f/\\:*?"<>|\[\]]+')


def say(name: str, post: dict) -> str:
    """Post one message to his DM through cc-slack and keep the same item on the drive's dashboard; a post in an order's thread is recorded on that order too."""
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
            line += "; " + cli.record_post(name, order, reply["permalink"], post.get("part"))[0]
        except (DriveError, slack.SlackError) as error:
            line += f"; not recorded on {order}: {error}"
    return line


def dry(name: str, post: dict) -> str:
    """What say would post, its references resolved and its visuals drawn, with nothing posted."""
    text = post.get("text") or ""
    drawn_ones = [drawn(name, index, visual) for index, visual in enumerate(post.get("visuals") or [], start=1)]
    where = f"thread {post['thread_ts']}" if post.get("thread_ts") else "his DM root"
    card = f", card {post['card'].get('title')!r}" if post.get("card") else ""
    prs = f", PR card for {len(post['tap_prs'])} PRs" if post.get("tap_prs") else ""
    return f"DRY {where}, {len(drawn_ones)} pictures{card}{prs}: {refs.slack(text, refs.resolved(name, text))}"


def posted(name: str, post: dict) -> dict:
    """cc-slack's reply to the post, which went out with its references linked and each visual drawn as a picture, and is kept as a said event for the dashboard, which draws each visual live from its source and each PR of a stack card with its state now."""
    text = post.get("text") or ""
    found = refs.resolved(name, text)
    visuals = [
        {"kind": kind(visual), "source": visual["source"], "caption": visual.get("caption") or "", "picture": str(drawn(name, index, visual))}
        for index, visual in enumerate(post.get("visuals") or [], start=1)
    ]
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
    argv += [flag for visual in visuals for flag in ("--file", visual["picture"])]
    if text:
        argv += ["--", refs.slack(text, found)]
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


def drawn(name: str, index: int, visual: dict) -> Path:
    """The picture Slack shows for one visual, named after its caption, which Slack titles it with: code highlighted, a diagram, chart or compare drawn as the dashboard draws it, an image as it is."""
    source = visual["source"]
    if visual["kind"] == "image" and not source.startswith(WEB):
        if not (local := Path(source).expanduser()).is_absolute() or not local.is_file():
            raise DriveError(f"visual {index}: {source} is neither a link nor a file here")
        source = local.as_uri()
    if visual["kind"] == "code" and (what := picture.raw(visual.get("language") or "", source)):
        raise DriveError(f"visual {index} is {what}, raw data or tool output, so nothing was posted; {picture.INSTEAD}")
    try:
        return picture.cached(state.drive_dir(name) / "pictures", " ".join(UNNAMEABLE.sub(" ", visual.get("caption") or "").split()) or "show-me", kind(visual), source)
    except picture.PictureError as error:
        raise DriveError(f"visual {index} ({visual['kind']}) does not draw, so nothing was posted: {error}") from None
