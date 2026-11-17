"""His words that no lane holds, in his DM or in a channel no lane has as its home, are routed in the call that hears them by one Jev pick: words about a lane's work go to that lane with lane message, which wakes its owner, and he hears in his thread which lane has them; chatter gets nothing; and only words that start new work or need real reading start one Opus intake session, by intake.md, the old communicator's turn for one message. His further messages in that channel within JOIN of a session's start join it instead of starting another. Its reply goes through the voice like any lane's post."""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from lane import session, store, supervisor, verbs, voice

INTAKES = "intake"
PAGE = Path(__file__).with_name("intake.md")
COMMUNICATOR = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference" / "communicator.md"
WORKSPACE = re.compile(r"https://([\w-]+\.slack\.com)/")
LANE = "intake"
JOIN = timedelta(minutes=1)
ROUTE_SECONDS = 5.0
READ = "read"
CHATTER = "chatter"


def folder(drive: str) -> Path:
    return store.drive_dir(drive) / INTAKES


def workspace(drive: str, link: str | None) -> str:
    """The Slack workspace host his links name: his message's, else any lane ask's."""
    links = [link or "", *(ask.get("link") or "" for lane in store.load_all(drive).values() for ask in lane.get("asks", []))]
    return next((found[1] for each in links if (found := WORKSPACE.match(each))), "slack.com")


def prompt(drive: str, channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    config = store.config(drive)
    return PAGE.read_text().format(
        drive=drive,
        channel=channel,
        ts=ts,
        thread_note=f", in the thread of {thread}" if thread else "",
        reply_thread=thread or ts,
        words=words.replace("\n", "\n> "),
        queue=folder(drive) / f"{channel}.jsonl",
        link=link or "none",
        lanes="\n".join(supervisor.check(drive)[1]),
        lanes_dir=store.drive_dir(drive),
        lane_bin=store.launcher(drive),
        checkout=config.get("checkout") or "the drive config names none",
        active=config["active"],
        workspace=workspace(drive, link),
        communicator=COMMUNICATOR,
        voice=voice.ROLE_PAGE,
    )


def heard(drive: str, channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    """Route his message in the call that hears it: to the lane it is about, to nothing for chatter, else to an Opus intake session; once per message, so a second hearing of it does nothing."""
    folder(drive).mkdir(parents=True, exist_ok=True)
    try:
        (folder(drive) / f"{ts}.seen").touch(exist_ok=False)
    except FileExistsError:
        return f"intake of {ts}: already heard"
    if words.startswith('tapped "'):
        return tapped(drive, channel, words, link, ts) or f"intake of {ts}: a tap on no question of the intake's"
    picked = route(drive, words)
    if picked == CHATTER:
        return f"intake of {ts}: chatter, nothing to do"
    if picked != READ:
        from lane import events

        lane = store.load(drive, picked)
        delivered = events.message(drive, picked, words, link, ts)
        reply(drive, channel, thread or ts, f"<https://{workspace(drive, link)}/archives/{lane['channel']}|{lane['title']}> has this" + (f": {lane['says']}" if lane.get("says") else "."))
        return f"intake of {ts}: {delivered}"
    return start(drive, channel, thread, words, link, ts)


def route(drive: str, words: str) -> str:
    """One Jev pick of where his words go: a lane's id, chatter, or read, which a failed pick falls to, since only a session can be sure."""
    from drive import settled

    lanes = {lane_id: {"title": lane["title"], "plan": lane.get("says") or lane.get("next") or ""} for lane_id, lane in store.load_all(drive).items() if lane["state"] != store.PARKED}
    options = {READ: "it starts new work no lane holds, needs reading several lanes or their files to answer, or you are unsure", CHATTER: "it asks and orders nothing, such as ok or thanks"} | {lane_id: f"it is about this lane's work: {lane['title']}" for lane_id, lane in lanes.items()}
    try:
        return str(settled.jev({"message": words, "lanes": lanes}, {"to": settled.Pick("Where does `message`, his words, go?", options)}, ROUTE_SECONDS, "intake")["to"])
    except settled.Unheard:
        return READ


def start(drive: str, channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    """Start one Opus intake session for his message, detached so the call that heard it returns at once, or join the session that started in his channel within JOIN, which reads his later messages from its queue before it ends."""
    queue = folder(drive) / f"{channel}.jsonl"
    if (opened := folder(drive) / f"{channel}.started").is_file() and store.now() - store.parse(opened.read_text().strip()) < JOIN:
        with queue.open("a") as out:
            out.write(json.dumps({"ts": ts, "thread": thread, "words": words, "link": link}, ensure_ascii=False) + "\n")
        return f"intake of {ts}: joined the intake that started at {opened.read_text().strip()}"
    opened.write_text(store.stamp())
    queue.write_text("")
    with (folder(drive) / f"{ts}.log").open("w") as out:
        subprocess.Popen(session.argv(store.config(drive), "intake", store.drive_dir(drive), {}, ["-p"], ["--permission-mode", "bypassPermissions", "--", prompt(drive, channel, thread, words, link, ts)]), cwd=store.drive_dir(drive), stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return f"intake of {ts}: an intake session started"


def reply(drive: str, channel: str, thread: str, text: str, options: list[str] | None = None) -> str:
    """The intake's one reply in his thread, through the voice with the role page beside it; a refusal comes back with its reason."""
    body = {"lane": LANE, "drive": drive, "channel_id": channel, "thread_ts": thread, "text": text, "words": text, "prs": [], "about": [], "rules": str(voice.ROLE_PAGE), "asked": "", **({"options": options} if options else {})}
    request = urllib.request.Request(voice.VOICE_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())})
    try:
        with urllib.request.urlopen(request, timeout=voice.ENQUEUE_SECONDS) as answer:
            queued = json.load(answer)
    except urllib.error.HTTPError as refused:
        if refused.code == 422:
            raise store.Refused(refused.read().decode().strip()) from refused
        raise
    return f"QUEUED {queued['id']}" + (f"; {queued['warning']}" if queued.get("warning") else "")


PENDING = "pending.json"
PARK = "Park {lane}"


def pending(drive: str) -> list[dict]:
    path = folder(drive) / PENDING
    return json.loads(path.read_text()) if path.is_file() else []


def ask_park(drive: str, channel: str, thread: str, text: str, opening: dict) -> str:
    """At the cap, ask him which lane to park for the lane opening would open, by his rule that a 13th lane names the lane it parks: one button per active lane, least urgent first by the last rank, the first recommended; the open waits, nothing parked, until his tap, which tapped carries out in the call that hears it."""
    from lane import rank

    lanes = {lane_id for lane_id, lane in store.load_all(drive).items() if lane["state"] != store.PARKED}
    order = [lane_id for lane_id in rank.last(drive).get("order", []) if lane_id in lanes]
    least = [*reversed(order), *sorted(lanes - set(order))]
    options = [PARK.format(lane=lane_id) for lane_id in least]
    sent = reply(drive, channel, thread, text, options)
    kept = [*pending(drive), {"channel": channel, "thread": thread, "options": options, "open": opening, "at": store.stamp()}]
    (folder(drive) / PENDING).write_text(json.dumps(kept, ensure_ascii=False) + "\n")
    return sent


def tapped(drive: str, channel: str, words: str, link: str | None, ts: str) -> str | None:
    """His tap on a park question the intake asked: park the lane he picked and open the waiting lane in this call, then tell him in its thread; None when the tap answers no such question."""
    label = words.removeprefix('tapped "').partition('"')[0]
    asked = next((each for each in pending(drive) if each["channel"] == channel and label in each["options"]), None)
    if not asked:
        return None
    parks = asked["options"][asked["options"].index(label)].removeprefix(PARK.format(lane=""))
    lane = verbs.open_lane(drive, **asked["open"], parks=parks, park_words=f"{words} ({link or ts})")
    (folder(drive) / PENDING).write_text(json.dumps([each for each in pending(drive) if each != asked], ensure_ascii=False) + "\n")
    reply(drive, channel, asked["thread"], f"Parked {parks} and opened {lane['id']}: {lane['title']}.")
    return f"intake of {ts}: parked {parks}, opened {lane['id']}"
