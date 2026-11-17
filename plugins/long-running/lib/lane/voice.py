"""A lane owner's say: lane.say writes the promise, and this sender hands the post to the cc-slack daemon's voice sender and returns at once.

The owner never waits on Jev or Slack. The daemon formats the post, has Jev move or cut sentences against the role page, and posts it in the lane's channel and thread, end to end in under a second; its before and after land in cc-slack's voice.jsonl, which `cc-slack voice --last N` prints.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from lane import page, say, store, verbs
from lane.words import short

LANE_ENV = "LONG_RUNNING_LANE"
TOKEN_ENV = "LANE_TOKEN"
VOICE_URL = "http://127.0.0.1:7378/voice"
ENQUEUE_SECONDS = 2
ROLE_PAGE = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference" / "voice.md"
PR_REF = re.compile(r"\{pr:(?:([\w.-]+/[\w.-]+)#)?#?(\d+)(?:\|([^{}]*))?\}")
THREAD_OF = re.compile(r"[?&]thread_ts=(\d+\.\d+)")
SEGMENTS = re.compile(r"(<[^<>]*>)")
BARE_PR_LINK = re.compile(r"<https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)\|#\2>")
BARE_NUMBER = re.compile(r"(?<![\w&/#])#(\d{3,})\b")
BARE_PR_URL = re.compile(r"\s*\(?(?<![<|])(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)\b\)?")
LINKED_PR = re.compile(r"<https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)\|")
TAPPED_PR = re.compile(r'^tapped "(Approve|Hold) #(\d+)"')


def own_lane() -> tuple[str, str] | None:
    """The drive and lane of the owner session calling say, from LONG_RUNNING_LANE as `<drive>/<lane>`; None for every other session."""
    if not (named := os.environ.get(LANE_ENV)):
        return None
    drive, _, lane_id = named.partition("/")
    return drive, lane_id


def owner_say(drive: str, lane_id: str, arguments: dict) -> str:
    """The MCP say of a lane's owner: lane.say with its promise, and when the promise's shape is refused, the post alone, since the owner never sees a post refused."""
    token = os.environ.get(TOKEN_ENV) or None
    try:
        return say.say(drive, lane_id, arguments["text"], arguments.get("next"), arguments.get("by"), token, arguments.get("answers"), arguments.get("re"), arguments.get("options"))
    except store.Refused as refused:
        if not (arguments.get("next") or arguments.get("by")):
            raise
        return f"{say.say(drive, lane_id, arguments['text'], token=token, answers=arguments.get('answers'), re=arguments.get('re'), options=arguments.get('options'))}; its promise was not kept: {refused}"


def send(drive: str, lane: dict, text: str, about_ts: str | None = None, options: list[str] | None = None) -> str:
    """lane.say's SENDER: queue one post in the lane's channel, in the thread of the ask it is about, with the role page beside it and a button for each option, and return QUEUED and its id."""
    seen = page.view(drive)["prs"]
    drawn = linked(text, titled(lane.get("prs") or [], seen))
    body = {
        "lane": lane["id"],
        "drive": drive,
        "channel_id": lane["channel"],
        "thread_ts": asked_thread(lane, about_ts),
        "text": drawn,
        "prs": named_prs(drawn, seen),
        "rules": str(ROLE_PAGE),
        "asked": "",
        **({"options": options} if options else {}),
    }
    request = urllib.request.Request(VOICE_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getppid())})
    try:
        with urllib.request.urlopen(request, timeout=ENQUEUE_SECONDS) as answer:
            return f"QUEUED {json.load(answer)['id']}"
    except urllib.error.HTTPError as refused:
        if refused.code == 422:
            raise store.Refused(refused.read().decode().strip()) from refused
        raise


def named_prs(text: str, seen: dict[str, dict]) -> list[dict]:
    """Each PR the post links, once, with its GitHub state as view.json read it, so the voice can give an ask for his Approve its buttons and refuse one about a PR already merged or closed."""
    keys = dict.fromkeys(f"{repo}#{number}" for repo, number in LINKED_PR.findall(text))
    return [{"repo": key.partition("#")[0], "number": int(key.partition("#")[2]), "state": (seen.get(key) or {}).get("state") or ""} for key in keys]


def asked_thread(lane: dict, about_ts: str | None) -> str:
    """The thread a post goes in: that of the ask of his it names with --re (about_ts), or, with none, of the one ask of his in the lane's channel not yet done when exactly one is, so every post about an ask, follow-ups included, stays under his message; any other post is news and goes at the channel's root, by his words "for prio lanes most things that aren't immediate replies to the thread or directly relevant should be channel messages"."""
    asks = [ask for ask in lane.get("asks") or [] if ask.get("ts")]
    if about_ts:
        if not (named := [ask for ask in asks if ask["ts"] == about_ts]):
            raise store.Refused(f"--re {about_ts} names no ask of his in lane {lane['id']}; `lane asks` lists them")
        about = named[0]
    elif len(open_asks := [ask for ask in asks if not ask.get("done") and f"/archives/{lane['channel']}/" in (ask.get("link") or "")]) == 1:
        about = open_asks[0]
    else:
        return ""
    if f"/archives/{lane['channel']}/" not in (about.get("link") or ""):
        return ""
    return (found[1] if (found := THREAD_OF.search(about["link"])) else None) or about["ts"]


def titled(prs: list[dict], seen: dict[str, dict]) -> list[dict]:
    """The lane's PR rows, each with no words of its own given a few from its GitHub title as the supervisor's last pass read it into view.json, then every other PR of the drive's lanes that view.json holds, so a #N any lane holds never goes out as a bare number."""
    held = {f"{row['repo']}#{row['number']}" for row in prs}
    own = [row if row.get("words") or not (pull := seen.get(f"{row['repo']}#{row['number']}")) else row | {"words": short(pull["title"])} for row in prs]
    return own + [{"repo": key.partition("#")[0], "number": int(key.partition("#")[2]), "words": short(pull["title"])} for key, pull in seen.items() if key not in held and pull.get("title")]


def linked(text: str, prs: list[dict]) -> str:
    """The text with each {pr:N} or {pr:owner/repo#N|words} the lane's prs rows hold drawn as `<url|#N words>`, the row's own words when the post gives none; one named by owner/repo#N links to it whether a lane holds it or not, and a bare number no lane holds stays as typed, which the sender drops as a dead link."""

    def drawn(match: re.Match[str]) -> str:
        repo, number = match[1], int(match[2])
        held = [row for row in prs if row["number"] == number and (repo is None or row["repo"].casefold() == repo.casefold())] or ([{"repo": repo, "number": number}] if repo else [])
        if len(held) != 1:
            return match[0]
        words = (match[3] or held[0].get("words") or "").strip()
        return f"<https://github.com/{held[0]['repo']}/pull/{number}|{' '.join(filter(None, [f'#{number}', words]))}>"

    return once("".join(worded(piece, prs) for piece in SEGMENTS.split(PR_REF.sub(drawn, text))))


def once(text: str) -> str:
    """The text with a bare PR URL, or one in parentheses, dropped when that PR is already linked in the post, so a PR never reads as two links."""
    linked_urls = {piece[1:].partition("|")[0] for piece in SEGMENTS.findall(text)}
    return "".join(piece if piece.startswith("<") else BARE_PR_URL.sub(lambda match: "" if match[1] in linked_urls else match[0], piece) for piece in SEGMENTS.split(text))


def worded(piece: str, prs: list[dict]) -> str:
    """One piece of a post as linked draws it: a link to a lane PR labelled with its bare number gains the row's words, and a bare #N the lane holds outside any link becomes that link, so every PR reads as `#N words`."""
    if bare := BARE_PR_LINK.fullmatch(piece):
        row = next((row for row in prs if row["repo"].casefold() == bare[1].casefold() and row["number"] == int(bare[2])), None)
        return f"<https://github.com/{bare[1]}/pull/{bare[2]}|#{bare[2]} {row['words']}>" if row and row.get("words") else piece
    if piece.startswith("<"):
        return piece

    def named(match: re.Match[str]) -> str:
        held = [row for row in prs if row["number"] == int(match[1])]
        return f"<https://github.com/{held[0]['repo']}/pull/{match[1]}|{' '.join(filter(None, [f'#{match[1]}', held[0].get('words')]))}>" if len(held) == 1 else match[0]

    return BARE_NUMBER.sub(named, piece)


say.SENDER = send


def heard(channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    """His message or tap in a lane's channel, as cc-slack's heard_command pushes it, appended to each lane it is for, which wakes its owner: in a thread, the lane whose thread it is; at the channel's root, the lane holding the channel with no thread; and when no lane or several lanes fit, every lane in the channel, so it reaches at least one and never none. A channel no lane holds, such as his DM where he taps most cards, keeps only his presence on every drive, so a dangerous land counts a tap there."""
    drives = [drive.name for drive in sorted(store.root().iterdir()) if drive.is_dir()] if store.root().is_dir() else []
    found = [(drive, lane_id, lane) for drive in drives for lane_id, lane in store.load_all(drive).items() if lane["channel"] == channel]
    fitting = [each for each in found if (each[2].get("thread") or None) == (thread or None)]
    chosen = fitting if len(fitting) == 1 else found
    if not chosen:
        for drive in drives:
            store.heard(drive, [{"ts": ts, "link": link}])
        return f"no lane holds {channel}; his presence is kept"
    from lane import approve

    for drive, lane_id, lane in chosen:
        if (landed := approve.tapped(drive, lane, words, ts, link)) is not None:
            return f"{lane_id}: his tap on {', '.join(landed)}"
        tapped_pr(drive, lane, words, link, ts)
    return "; ".join(f"{lane_id}: {verbs.message(drive, lane_id, words, link, ts)} new" for drive, lane_id, _ in chosen)


def tapped_pr(drive: str, lane: dict, words: str, link: str | None, ts: str) -> None:
    """His tap on an Approve #N or Hold #N button the voice attached, written as his Approve or Hold on that lane PR at the head view.json read, with the tap's ts as his presence, as a card tap is; his words still reach the lane beside it."""
    if not (found := TAPPED_PR.match(words)):
        return
    label, number = found[1], int(found[2])
    rows = [row for row in lane.get("prs") or [] if row["number"] == number]
    if len(rows) != 1 or not (head := (page.view(drive)["prs"].get(f"{rows[0]['repo']}#{number}") or {}).get("head")):
        return
    verbs.tapped(drive, rows[0]["repo"], number, label, head, link or "his tap on the voice's buttons", ts, named=True)
