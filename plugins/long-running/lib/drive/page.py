import json
import subprocess

from drive import state
from drive.render import when

GROUP_TITLES = {
    "blocked": "Blocked",
    "running": "Running",
    "waiting": "Waiting",
    "merged": "Merged, not live yet",
    "live": "Live in the last day",
    "not started": "Not started",
}
KIND_WORDS = {"question": "Question", "tap": "Your tap", "late": "Late"}
CHECK_MARKS = {"pass": ":large_green_circle:", "fail": ":red_circle:", "pending": ":large_yellow_circle:"}
TABLE_ROWS = 90
TABLE_HEAD = ["| Feature | Now | Pull requests |", "|---|---|---|"]


class PageError(Exception):
    pass


def cell(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def link(title: str, url: str | None) -> str:
    text = cell(title).replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url else text


def queue_line(number: int, item: dict) -> str:
    parts = [f"**{KIND_WORDS[item['kind']]}:** {link(item['title'], item['url'])}"]
    if item["kind"] == "late":
        parts.append(f"promised {when(item['due'])}")
    elif item["since"]:
        parts.append(f"since {when(item['since'])}")
    if item["detail"]:
        parts.append(cell(item["detail"]))
    parts.extend(link(extra["label"], extra["url"]) for extra in item["links"])
    return f"{number}. " + " · ".join(parts)


def pr_mark(pr: dict) -> str:
    mark = CHECK_MARKS.get(pr["checks"], ":white_circle:")
    held = " held by you" if pr["tap"] == "Hold" else ""
    return f"{mark} {link(pr['title'], pr['url'])}{held}"


def pr_cell(prs: list[dict]) -> str:
    merged = sum(pr["state"] == "merged" for pr in prs)
    closed = sum(pr["state"] == "closed" for pr in prs)
    settled = ", ".join(f"{count} {word}" for count, word in ((merged, "merged"), (closed, "closed")) if count)
    return "<br>".join([*(pr_mark(pr) for pr in prs if pr["state"] == "open"), *([settled] if settled else [])]) or " "


def feature_row(entry: dict) -> str:
    title = link(entry["title"], entry["url"])
    if entry["due"]:
        title += f"<br>{'late, was due' if entry['late'] else 'due'} {when(entry['due'])}"
    prs = pr_cell(entry["prs"])
    return f"| {title} | {cell(entry['now']) or ' '} | {prs} |"


def render(model: dict) -> str:
    """The Slack status page of a drive's model: what waits on him first, then each feature by state."""
    counts = model["counts"]
    summary = " · ".join(
        [f"{counts['queue']} waiting on you"] + [f"{counts[name]} {GROUP_TITLES[name].lower()}" for name in GROUP_TITLES if counts[name]]
    )
    lines = [f"Updated {when(model['at'])} · {summary}", "", "## Waiting on you", ""]
    lines += [queue_line(number, item) for number, item in enumerate(model["queue"], 1)] or ["Nothing waits on you."]
    if model.get("cards", "live") != "live":
        lines += ["", f"Your Slack questions are not listed here: cc-slack {cell(model['cards'])}"]
    for group in model["status"]:
        if not group["features"]:
            continue
        lines += ["", f"## {GROUP_TITLES[group['state']]} ({len(group['features'])})"]
        for start in range(0, len(group["features"]), TABLE_ROWS):
            lines += ["", *TABLE_HEAD, *map(feature_row, group["features"][start : start + TABLE_ROWS])]
    return "\n".join(lines) + "\n"


def draw(name: str, markdown: str) -> str:
    """Draw the page on the drive's canvas, creating it as a tab in his DM the first time; returns the page's URL."""
    canvas = state.load(name)["slack"].get("page")
    argv = ["cc-slack", "canvas", "--title", f"{name}: where it stands"]
    if canvas:
        argv += ["--canvas", canvas]
    try:
        result = subprocess.run(argv, input=markdown, check=False, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PageError(str(error)) from None
    if result.returncode:
        raise PageError(" ".join(result.stderr.split()) or f"cc-slack exited {result.returncode}")
    drawn = json.loads(result.stdout)
    if drawn["canvas_id"] != canvas:
        with state.edit(name) as data:
            data["slack"]["page"] = drawn["canvas_id"]
    return drawn["url"]
