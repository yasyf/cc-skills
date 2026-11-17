"""A lane owner's say: lane.say writes the promise, and this sender hands the post to the cc-slack daemon's voice sender and returns at once.

The owner never waits on Jev or Slack. The daemon formats the post, has Jev move or cut sentences against the role page, and posts it in the lane's channel and thread, end to end in under a second; its before and after land in cc-slack's voice.jsonl, which `cc-slack voice --last N` prints.
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lane import page, say, store, verbs
from lane.words import pr_words

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
PERMALINK_TS = re.compile(r"/p(\d{10})(\d{6})")
SLACK_MESSAGE = re.compile(r"https://[\w-]+\.slack\.com/archives/(C\w+)/p(\d{10})(\d{6})")
ATTACHED_APPROVE = re.compile(r"^Approve #(\d+)$")
PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
BUTTON_CLAUSE = re.compile(r"[,;:](?=\s)| - | — ")
BUTTON_WORDS = 4
BUTTON_CHARS = 24
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
        return say.say(drive, lane_id, arguments["text"], arguments.get("next"), arguments.get("by"), token, arguments.get("answers"), arguments.get("re"), arguments.get("options"), edit_ts(arguments.get("edit")), arguments.get("subjects"), arguments.get("pictures"))
    except store.Refused as refused:
        if not (arguments.get("next") or arguments.get("by")):
            raise
        return f"{say.say(drive, lane_id, arguments['text'], token=token, answers=arguments.get('answers'), re=arguments.get('re'), options=arguments.get('options'), edit=edit_ts(arguments.get('edit')), about=arguments.get('subjects'), pictures=arguments.get('pictures'))}; its promise was not kept: {refused}"


def status(drive: str, lane_id: str, line: str, about_ts: str | None) -> str:
    """Set the loader line under the thread of his ask, where a status update belongs in place of a whole message, by his words "these kind of status updates should just be in the spinner/loader slack agent thing"; Slack draws it only in his DM's threads, and a refusal says so."""
    lane = store.load(drive, lane_id)
    if not (thread := asked_thread(lane, about_ts)):
        raise store.Refused("a loader line goes under a thread of his: pass --re <his ask's ts>")
    body = json.dumps({"channel_id": lane["channel"], "thread_ts": thread, "status": line}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(STATUS_URL, body, {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())}), timeout=ENQUEUE_SECONDS * 5):
            return f"loader set under {thread}"
    except urllib.error.HTTPError as refused:
        if refused.code == 422:
            raise store.Refused(refused.read().decode().strip()) from refused
        raise


def edit_ts(edit: str | None) -> str | None:
    """The ts of the post an edit names, given as its ts or, as the MCP say's edit takes it, as its permalink."""
    return f"{found[1]}.{found[2]}" if edit and (found := PERMALINK_TS.search(edit)) else edit


def send(drive: str, lane: dict, text: str, about_ts: str | None = None, options: list[str] | None = None, edit: str | None = None, about: list[str] | None = None, pictures: list[str] | None = None) -> str:
    """lane.say's SENDER: queue one post in the lane's channel, in the thread of the ask it is about, with the role page beside it and a button for each option, and return QUEUED and its id."""
    if options:
        text, options = buttoned(text, options)
    seen = page.view(drive)["prs"]
    drawn = linked(text, titled(lane.get("prs") or [], seen))
    body = {
        "lane": lane["id"],
        "drive": drive,
        "channel_id": lane["channel"],
        "thread_ts": asked_thread(lane, about_ts),
        "text": drawn,
        "prs": named_prs(drawn, seen, lane.get("prs") or []),
        "about": subjects(lane, drawn, about_ts, about or []),
        "rules": str(ROLE_PAGE),
        "asked": "",
        **({"options": options} if options else {}),
        **({"edit_ts": edit} if edit else {}),
        **({"files": [str(Path(picture).expanduser().resolve()) for picture in pictures]} if pictures else {}),
    }
    request = urllib.request.Request(VOICE_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())})
    try:
        with urllib.request.urlopen(request, timeout=ENQUEUE_SECONDS) as answer:
            queued = json.load(answer)
    except urllib.error.HTTPError as refused:
        if refused.code == 422:
            raise store.Refused(refused.read().decode().strip()) from refused
        raise
    carded(drive, lane["id"], queued["id"], queued.get("attached") or [], seen)
    return f"QUEUED {queued['id']}" + (f"; {queued['warning']}" if queued.get("warning") else "")


def button(option: str) -> str:
    """An option as a button Slack shows whole: its parentheticals, such as (Recommended), dropped, cut at its first clause, at most BUTTON_WORDS words and BUTTON_CHARS characters, by his words "the buttons are all cut off I have no idea what they're saying"."""
    words = BUTTON_CLAUSE.split(PARENTHETICAL.sub("", option).strip(), maxsplit=1)[0].split()
    kept: list[str] = []
    for word in words[:BUTTON_WORDS]:
        if len(" ".join([*kept, word])) > BUTTON_CHARS:
            break
        kept.append(word)
    return " ".join(kept)


def buttoned(text: str, options: list[str]) -> tuple[str, list[str]]:
    """A question's text and buttons as he can read them: each option shortened to a button, and when any was shortened, each listed in full under its button's name at the post's end; options that shorten to an empty or repeated button go on as given, for the voice to refuse with the rule."""
    labels = [button(option) for option in options]
    if not all(labels) or len({label.casefold() for label in labels}) < len(labels):
        return text, options
    if labels == [option.strip() for option in options]:
        return text, labels
    listed = "\n".join(f"{n}. *{label}*: {option.strip()}" for n, (label, option) in enumerate(zip(labels, options, strict=True), 1))
    return f"{text.rstrip()}\n\n{listed}", labels


def named_prs(text: str, seen: dict[str, dict], rows: list[dict] = ()) -> list[dict]:
    """Each PR the post links, once, with its GitHub state and head as view.json read them and whether his Approve stands at that head, so the voice can give an ask for his Approve its buttons and refuse one about a PR already merged, closed or approved."""
    keys = dict.fromkeys(f"{repo}#{number}" for repo, number in LINKED_PR.findall(text))
    approved = {f"{row['repo']}#{row['number']}": row.get("approved_head") for row in rows}
    return [{"repo": key.partition("#")[0], "number": int(key.partition("#")[2]), "state": (pull := seen.get(key) or {}).get("state") or "", "head": pull.get("head") or "", "approved": bool(pull.get("head")) and approved.get(key) == pull.get("head")} for key in keys]


def subjects(lane: dict, text: str, about_ts: str | None, about: list[str]) -> list[dict]:
    """What else a post is about, beside the PRs it links: the ask --re names, each card of the lane's channel it links, and each `release:<ref>` or `card:<ts>` its owner names with --about."""
    found = [{"kind": "ask", "ts": about_ts}] if about_ts else []
    found += [{"kind": "card", "ts": f"{seconds}.{micros}"} for channel, seconds, micros in SLACK_MESSAGE.findall(text) if channel == lane["channel"]]
    for named in about:
        kind, _, ref = named.partition(":")
        if kind not in ("release", "card") or not ref:
            raise store.Refused(f"--about {named!r} names release:<ref> or card:<ts>")
        found.append({"kind": kind, "ts" if kind == "card" else "ref": ref})
    return found


def carded(drive: str, lane_id: str, post_id: str, attached: list[str], seen: dict[str, dict]) -> None:
    """Mark each lane PR the voice attached Approve and Hold buttons to as carded at its head by this post, the record the supervisor reads so it never draws a second card for the same head."""
    numbers = {int(found[1]) for label in attached if (found := ATTACHED_APPROVE.match(label))}
    if not numbers:
        return

    def marking(lane: dict) -> None:
        for row in lane["prs"]:
            if row["number"] in numbers and (head := (seen.get(f"{row['repo']}#{row['number']}") or {}).get("head")):
                row["carded"] = {"head": head, "by": post_id, "at": store.stamp()}

    store.update(drive, lane_id, marking)


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
    own = [row if row.get("words") or not (pull := seen.get(f"{row['repo']}#{row['number']}")) else row | {"words": pr_words(row["repo"], row["number"], pull["title"])} for row in prs]
    return own + [{"repo": key.partition("#")[0], "number": int(key.partition("#")[2]), "words": pr_words(key.partition("#")[0], int(key.partition("#")[2]), pull["title"])} for key, pull in seen.items() if key not in held and pull.get("title")]


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
    """His message or tap in a lane's channel, as cc-slack's heard_command pushes it, appended to each lane it is for, which wakes its owner: in a thread, the lane whose thread it is; at the channel's root, the lane holding the channel with no thread; and when no lane or several lanes fit, every lane in the channel, so it reaches at least one and never none. A channel no lane holds, such as his DM, reaches no lane."""
    drives = [drive.name for drive in sorted(store.root().iterdir()) if drive.is_dir()] if store.root().is_dir() else []
    found = [(drive, lane_id, lane) for drive in drives for lane_id, lane in store.load_all(drive).items() if lane["channel"] == channel]
    fitting = [each for each in found if (each[2].get("thread") or None) == (thread or None)]
    chosen = fitting if len(fitting) == 1 else found
    if not chosen:
        return f"no lane holds {channel}"
    from lane import approve

    for drive, lane_id, lane in chosen:
        if (landed := approve.tapped(drive, lane, words, ts, link)) is not None:
            return f"{lane_id}: his tap on {', '.join(landed)}"
        tapped_pr(drive, lane, words, link, ts)
    return "; ".join(f"{lane_id}: {verbs.message(drive, lane_id, words, link, ts)} new" for drive, lane_id, _ in chosen)


def tapped_pr(drive: str, lane: dict, words: str, link: str | None, ts: str) -> None:
    """His tap on an Approve #N or Hold #N button the voice attached, written as his Approve or Hold on that lane PR at the head view.json read; his words still reach the lane beside it."""
    if not (found := TAPPED_PR.match(words)):
        return
    label, number = found[1], int(found[2])
    rows = [row for row in lane.get("prs") or [] if row["number"] == number]
    if len(rows) != 1 or not (head := (page.view(drive)["prs"].get(f"{rows[0]['repo']}#{number}") or {}).get("head")):
        return
    verbs.tapped(drive, rows[0]["repo"], number, label, head, link or "his tap on the voice's buttons", ts, named=True)


POSTS_URL = VOICE_URL + "/posts"
STATUS_URL = VOICE_URL + "/status"
SETTLE_URL = VOICE_URL + "/settle"
FOLLOWED_HOURS = 24
SETTLED_FILE = "settled.json"
RETIRED = "✅ already approved; landing"
ASKS_HIS_TAP = re.compile(r"\btap\b|your approv", re.IGNORECASE)
PACIFIC = ZoneInfo("America/Los_Angeles")


def followed(drive: str, moment: datetime) -> list[dict]:
    """The lane posts of this drive the voice sent in the last FOLLOWED_HOURS, each with the PRs it named: those it recorded, or for a post from before it recorded them, the PRs its words link, their state and head then unknown."""
    since = (moment - timedelta(hours=FOLLOWED_HOURS)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with urllib.request.urlopen(urllib.request.Request(f"{POSTS_URL}?since={since}", headers={"X-Claude-Pid": str(os.getpid())}), timeout=ENQUEUE_SECONDS) as answer:
        listed = json.load(answer)
    lanes, taps = store.load_all(drive), listed.get("taps") or {}
    return [post | {"prs": post.get("prs") or named_prs(post.get("after") or "", {}), "about": [subject | ({"answer": taps.get(subject["ts"])} if subject["kind"] == "card" else {}) for subject in post.get("about") or []]} for post in listed.get("posts") or [] if post.get("lane") in lanes and post.get("ts")]


def subject_keys(posts: list[dict]) -> set[tuple[str, int]]:
    """Every PR a followed post names, read in the supervisor's one batched GitHub request with the lanes' own."""
    return {(pr["repo"], pr["number"]) for post in posts for pr in post["prs"]}


def settle(drive: str, posts: list[dict], prs: dict[tuple[str, int], dict]) -> list[str]:
    """Make each followed post follow what it named, his order: "messages should be tagged such that when the thing underneath them (such as an approval) changes they change state, automatically or by agent depending on the situation". A PR merged or closed after the post, or approved or held by him on a question naming it, is mechanical: the post gets one state line, such as "✅ #57894 merged 3:18 PM", and loses its buttons. A PR given a new head since the post is a judgment, since the post's claims about it may no longer hold: its owner is told, once, to edit it. What each post was given is kept in the drive's settled file, so nothing is done twice; the permalinks it changed."""
    path = store.drive_dir(drive) / SETTLED_FILE
    done = json.loads(path.read_text()) if path.is_file() else {}
    lanes = store.load_all(drive)
    changed = []
    releases = release_states(drive, {subject["ref"] for post in posts for subject in post["about"] if subject["kind"] == "release"})
    for post in posts:
        lane = lanes[post["lane"]]
        lines, stale = changes(post, lane, prs, releases)
        kept = done.setdefault(post["ts"], {})
        if lines and (line := " · ".join(lines)) != kept.get("line"):
            body = json.dumps({"channel_id": post["channel_id"], "ts": post["ts"], "thread_ts": post.get("thread_ts") or "", "line": line, "only_if_buttons": line == RETIRED}).encode()
            with urllib.request.urlopen(urllib.request.Request(SETTLE_URL, body, {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())}), timeout=ENQUEUE_SECONDS * 5) as answer:
                if permalink := json.load(answer).get("permalink"):
                    changed.append(permalink)
            kept["line"] = line
        if stale and (why := "; ".join(stale)) != kept.get("stale"):
            told = f"Your post {post['permalink']} is stale because {why}. If what it says no longer holds, edit it: `lane say --edit {post['ts']} \"<the post as it should read now>\"`."
            verbs.handover(drive, post["lane"], f"- {told}", "lane-voice")
            if lane["state"] == store.WAITING:
                verbs.wake(drive, post["lane"], told)
            kept["stale"] = why
    path.write_text(json.dumps(done))
    return changed


def after(stamp: str | None, posted: str) -> bool:
    return bool(stamp) and datetime.fromisoformat(stamp.replace("Z", "+00:00")) > datetime.fromisoformat(posted)


def clock(stamp: str) -> str:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(PACIFIC).strftime("%-I:%M %p")


def changes(post: dict, lane: dict, prs: dict[tuple[str, int], dict], releases: dict[str, dict]) -> tuple[list[str], list[str]]:
    """What changed under one post since it went out: the mechanical state lines it takes, for its PRs, the card it names being answered, the ask it answers being done and the release it names going live or failing, and the judgment reasons its owner is told of."""
    rows = {(row["repo"], row["number"]): row for row in lane.get("prs") or []}
    asks = {ask["ts"]: ask for ask in lane.get("asks") or [] if ask.get("ts")}
    asked = " ".join(post.get("options") or [])
    lines, stale = [], []
    if retired(post, rows, prs):
        return [RETIRED], []
    for pr in post["prs"]:
        key = (pr["repo"], pr["number"])
        if not (now := prs.get(key)):
            continue
        row = rows.get(key) or {}
        if now["merged"] and pr.get("state") != "MERGED" and after(now.get("merged_at"), post["at"]):
            lines.append(f"✅ #{pr['number']} merged {clock(now['merged_at'])}")
        elif now["state"] == "CLOSED" and pr.get("state") != "CLOSED" and after(now.get("closed_at"), post["at"]):
            lines.append(f"✖️ #{pr['number']} closed {clock(now['closed_at'])}")
        elif f"#{pr['number']}" in asked and row.get("held"):
            lines.append(f"⏸️ #{pr['number']} held")
        elif f"#{pr['number']}" in asked and row.get("approved_head") == now["head"]:
            lines.append(f"✅ #{pr['number']} approved")
        elif now["state"] == "OPEN" and pr.get("head") and now["head"] != pr["head"]:
            stale.append(f"#{pr['number']} has a new head, {now['head'][:7]}, since it read {pr['head'][:7]}")
    for subject in post.get("about") or []:
        if subject["kind"] == "card" and subject.get("answer"):
            lines.append(f"✅ answered: {subject['answer']}")
        elif subject["kind"] == "ask" and (asks.get(subject["ts"]) or {}).get("done"):
            lines.append("✅ done")
        elif subject["kind"] == "release" and (release := releases.get(subject["ref"])) and release["state"] in ("live", "failed"):
            lines.append(f"{'🚀' if release['state'] == 'live' else '❌'} {release['name']} {release['state']}")
    return lines, stale


def release_states(drive: str, refs: set[str]) -> dict[str, dict]:
    """Each release a followed post names, by its ref, as the release tool's own record reads it now: live once it converged, failed when it failed or converged on only some, started while it runs."""
    from drive import providers

    from lane import releases

    if not refs or not (checkout := store.config(drive)["checkout"]):
        return {}
    tool = providers.at(Path(checkout)).release
    return {ref: {"name": record.get("name") or ref, "state": releases.RESULTS.get(record["status"], "started")} for ref in refs if (record := tool.status(ref))}


def retired(post: dict, rows: dict[tuple[str, int], dict], prs: dict[tuple[str, int], dict]) -> bool:
    """Whether a live question asks about work he already decided: a post with buttons, or one asking his tap or Approve, whose open PRs he has not held all carry his Approve at their heads now, so the drive retires it as landing, its buttons off (a post whose buttons are gone already is left as it is)."""
    if not (post.get("options") or ASKS_HIS_TAP.search(post.get("after") or "")):
        return False
    asked = [(key, now) for pr in post["prs"] if (now := prs.get(key := (pr["repo"], pr["number"]))) and now["state"] == "OPEN" and not (rows.get(key) or {}).get("held")]
    return bool(asked) and all((rows.get(key) or {}).get("approved_head") == now["head"] for key, now in asked)
