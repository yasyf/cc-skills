import json
import re
import subprocess
from itertools import accumulate, takewhile
from typing import NamedTuple

from drive import slack, state
from drive.render import cut, when

PAGE_BYTES = 40_000
GROUP_TITLES = {
    "blocked": "Blocked",
    "running": "Running",
    "built": "Built, waiting on your design answers",
    "waiting": "Waiting",
    "merged": "Merged, not live yet",
    "live": "Live in the last day",
    "not started": "Not started",
}
OPEN_STATES = ("blocked", "running", "built", "waiting", "not started")
FINISHED_STATES = ("merged", "live")
FINISHED_NAMED = 5
KIND_WORDS = {"question": "Question", "tap": "Your tap", "late": "Late"}
BUILT_WORDS = "Built, paused on your pick"
CHECK_MARKS = {"pass": ":large_green_circle:", "fail": ":red_circle:", "pending": ":large_yellow_circle:"}
TITLE_CHARS = 100
DETAIL_CHARS = 100
NOW_CHARS = 100
REFUSAL = re.compile(r"can't post here \((\w+)\)")


class PageError(Exception):
    pass


class Drawn(NamedTuple):
    url: str
    new_tab: bool


class Section(NamedTuple):
    named: str
    lines: list[str]


def size(text: str) -> int:
    return len(text.encode())


def link(title: str, url: str | None) -> str:
    text = cut(title, TITLE_CHARS).replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url else text


def queue_line(number: int, item: dict) -> str:
    kind = BUILT_WORDS if item.get("built") else KIND_WORDS[item["kind"]]
    parts = [f"**{kind}:** {link(item['title'], item['url'])}"]
    if item.get("holds"):
        parts.append(f"holds up {item['holds']} finished")
    if item.get("blocks"):
        parts.append(f"blocks {'; '.join(cut(title, TITLE_CHARS) for title in item['blocks'])}")
    if item["kind"] == "late":
        parts.append(f"promised {when(item['due'])}")
    elif item["since"]:
        parts.append(f"since {when(item['since'])}")
    if item["detail"]:
        parts.append(cut(item["detail"], DETAIL_CHARS))
    parts.extend(link(extra["label"], extra["url"]) for extra in item["links"])
    return f"{number}. " + " · ".join(parts)


def pr_mark(pr: dict) -> str:
    mark = CHECK_MARKS.get(pr["checks"], ":white_circle:")
    held = " held by you" if pr["tap"] == "Hold" else ""
    return f"{mark} [#{pr['url'].rsplit('/', 1)[-1]}]({pr['url']}){held}"


def pr_marks(prs: list[dict]) -> str:
    settled = [f"{count} {word}" for word in ("merged", "closed") if (count := sum(pr["state"] == word for pr in prs))]
    return " ".join([*(pr_mark(pr) for pr in prs if pr["state"] == "open"), *settled])


def feature_line(entry: dict) -> str:
    parts = [link(entry["title"], entry["url"])]
    if entry["due"]:
        parts.append(f"{'late, was due' if entry['late'] else 'due'} {when(entry['due'])}")
    if entry["now"]:
        parts.append(cut(entry["now"], NOW_CHARS))
    if marks := pr_marks(entry["prs"]):
        parts.append(marks)
    parts.extend(map(built_line, entry["built"]))
    if entry["cards"]:
        parts.append("waits on your answer to " + "; ".join(f"{link(card['title'], card['url'])}, asked {when(card['asked'])}" for card in entry["cards"]))
    return "- " + " · ".join(parts)


def built_line(built: dict) -> str:
    return f"built on `{built['branch']}`, {'no PR yet' if built['waits'] else 'its PR opens next'}"


def finished_line(entry: dict) -> str:
    parts = [link(entry["title"], entry["url"]), entry["state"]]
    if entry["now"]:
        parts.append(cut(entry["now"], NOW_CHARS))
    return "- " + " · ".join(parts)


def feature_section(group: dict) -> Section:
    title = GROUP_TITLES[group["state"]]
    count = len(group["features"])
    return Section(f"{count} {title.lower()}", ["", f"## {title} ({count})", "", *map(feature_line, group["features"])])


def finished_section(groups: list[dict]) -> Section:
    features = sorted((entry for group in groups for entry in group["features"]), key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    counts = " · ".join(f"{len(group['features'])} {GROUP_TITLES[group['state']].lower()}" for group in groups)
    return Section(f"{len(features)} finished", ["", f"## Finished ({counts})", "", *map(finished_line, features[:FINISHED_NAMED])])


def compose(top: list[str], sections: list[Section], left: list[str], dashboard: str) -> str:
    lines = [*top, *(line for section in sections for line in section.lines), ""]
    if left:
        lines.append(f"Left out to fit Slack's canvas limit: {', '.join(left)}.")
    lines.append(f"The [dashboard]({dashboard}) has everything else.")
    return "\n".join(lines) + "\n"


def render(model: dict, dashboard: str) -> str:
    """The Slack status page of a drive's model, never over `PAGE_BYTES`: what waits on him in full and first, each open feature as one line by state, finished work as counts with the newest named, then the dashboard link. A page that would not fit leaves out its lowest-priority section first, finished work then each open state from the bottom, and only then the last items of his queue, naming what it left out in one line."""
    counts = model["counts"]
    summary = " · ".join(
        [f"{counts['queue']} waiting on you"] + [f"{counts[name]} {GROUP_TITLES[name].lower()}" for name in GROUP_TITLES if counts[name]]
    )
    head = [f"Updated {when(model['at'])} · {summary}", "", "## Waiting on you", ""]
    queue = [queue_line(number, item) for number, item in enumerate(model["queue"], 1)] or ["Nothing waits on you."]
    note = ["", f"Your Slack questions are not listed here: cc-slack {cut(model['cards'], DETAIL_CHARS)}"] if model.get("cards", "live") != "live" else []
    groups = {group["state"]: group for group in model["status"]}
    sections = [feature_section(groups[name]) for name in OPEN_STATES if groups[name]["features"]]
    if finished := [groups[name] for name in FINISHED_STATES if groups[name]["features"]]:
        sections.append(finished_section(finished))
    left: list[str] = []
    while sections and size(compose([*head, *queue, *note], sections, left, dashboard)) > PAGE_BYTES:
        left.insert(0, sections.pop().named)
    drawn = compose([*head, *queue, *note], sections, left, dashboard)
    if size(drawn) <= PAGE_BYTES:
        return drawn
    room = PAGE_BYTES - size(compose([*head, *note], [], [f"{len(queue)} more waiting on you", *left], dashboard))
    shown = len(list(takewhile(lambda total: total <= room, accumulate(size(line) + 1 for line in queue))))
    return compose([*head, *queue[:shown], *note], [], [f"{len(queue) - shown} more waiting on you", *left], dashboard)


def draw(name: str, markdown: str) -> Drawn:
    """Draw the page on the drive's canvas, creating it as a tab in his DM the first time, and again when the stored canvas is gone or Slack calls it full; returns the page's URL and whether it moved to a new tab."""
    canvas = state.load(name)["slack"].get("page")
    argv = ["canvas", "--title", f"{name}: where it stands"]
    if canvas:
        argv += ["--canvas", canvas]
    try:
        result = subprocess.run([slack.launcher(), *argv], input=markdown, check=False, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PageError(str(error)) from None
    if result.returncode:
        said = " ".join(result.stderr.split())
        refused = REFUSAL.search(said)
        raise PageError(f"Slack refused the redraw ({refused[1]})" if refused else said or f"cc-slack exited {result.returncode}")
    drawn = json.loads(result.stdout)
    if drawn["canvas_id"] != canvas:
        with state.edit(name) as data:
            data["slack"]["page"] = drawn["canvas_id"]
    return Drawn(drawn["url"], bool(canvas) and drawn["canvas_id"] != canvas)
