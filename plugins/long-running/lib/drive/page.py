import re
from collections import Counter
from collections.abc import Callable
from typing import NamedTuple
from urllib.parse import urlparse

from drive import slack, state
from drive.render import cut, when

ROWS = 8
STATE_MARKS = {"blocked": "🔴", "running": "🔵", "built": "🟣", "waiting": "⏳", "not started": "⚪", "merged": "🟢", "live": "✅"}
STATE_TITLES = {
    "blocked": "Blocked",
    "running": "Running",
    "built": "Built, waiting on your pick",
    "waiting": "Waiting",
    "not started": "Not started",
    "merged": "Merged, not live yet",
    "live": "Live in the last day",
}
OPEN_STATES = ("blocked", "running", "built", "waiting", "not started")
FINISHED_STATES = ("merged", "live")
CHECK_MARKS = {"pass": "🟢", "fail": "🔴", "pending": "🟡"}
HELD = {"Hold": " held by you", "labeled": " held by its hold label"}
PRS_SHOWN = 4
TITLE_CHARS = 80
CELL_CHARS = 90
NAME_CHARS = 40
PULL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
BARE_URL = re.compile(r"(?<!\]\()https?://[^\s)>|]*[^\s)>|.,;:!?]")
REFUSAL = re.compile(r"can't post here \((\w+)\)")


class PageError(Exception):
    pass


class Drawn(NamedTuple):
    url: str
    new_tab: bool


class Table(NamedTuple):
    heading: str
    columns: tuple[str, ...]
    rows: list[tuple[str, ...]]
    total: int
    rest: str


def url_label(url: str) -> str:
    pull = PULL.fullmatch(url)
    return f"#{pull[1]}" if pull else urlparse(url).hostname.split(".")[-2:][0]


def short_link(found: re.Match) -> str:
    return "…" if found[0].endswith("…") else f"[{url_label(found[0])}]({found[0]})"


def cell(text: str, limit: int = CELL_CHARS) -> str:
    flat = " ".join(text.split()).replace("|", "/")
    shown = BARE_URL.sub(lambda found: url_label(found[0]), flat)
    return BARE_URL.sub(short_link, flat if len(shown) <= limit else cut(flat, limit))


def link(title: str, url: str | None, limit: int = TITLE_CHARS) -> str:
    text = cut(title, limit).replace("|", "/").replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url else text


def pr_number(url: str) -> str:
    return f"[#{url.rsplit('/', 1)[-1]}]({url})"


def pr_marks(prs: list[dict]) -> str:
    marks = [f"{CHECK_MARKS.get(pr['checks'], '⚪')} {pr_number(pr['url'])}{HELD.get(pr['tap'], '')}" for pr in prs if pr["state"] == "open"]
    counted = [("more open", len(marks) - PRS_SHOWN), *((word, sum(pr["state"] == word for pr in prs)) for word in ("merged", "closed"))]
    return " ".join([*marks[:PRS_SHOWN], *(f"{count} {word}" for word, count in counted if count > 0)]) or "—"


def question_row(item: dict) -> tuple[str, ...]:
    holds = [f"{item['holds']} finished" if item["holds"] else "", *(cut(entry["title"], NAME_CHARS) for entry in item["blocks"])]
    return (
        ("🟣 " if item["built"] else "") + link(item["title"], item["url"]),
        cell("; ".join(filter(None, holds))) or "—",
        when(item["since"]),
        cell(item["recommended"] or f"Options: {item['detail']}"),
    )


def tap_row(item: dict) -> tuple[str, ...]:
    return (
        f"{link(item['title'], item['url'])} · {pr_number(item['pr'])}",
        f"{CHECK_MARKS.get(item['checks'], '⚪')} {cell(item['detail'])}",
        when(item["since"]) if item["since"] else "—",
    )


def late_row(item: dict) -> tuple[str, ...]:
    return (link(item["title"], item["url"]), when(item["due"]), cell(item["detail"]) or "—")


def built_words(built: dict) -> str:
    return f"🟣 built on `{built['branch']}`, {'no PR yet' if built['waits'] else 'its PR opens next'}"


def feature_row(entry: dict) -> tuple[str, ...]:
    due = f"<br>⏰ {'late, was due' if entry['late'] else 'due'} {when(entry['due'])}" if entry["due"] else ""
    cards = (f"❓ waits on {link(card['title'], card['url'], NAME_CHARS)}" for card in entry["cards"])
    now = [cell(entry["now"]), *map(built_words, entry["built"]), *cards]
    return (link(entry["title"], entry["url"]) + due, cell(entry["project"] or "—", NAME_CHARS), "<br>".join(filter(None, now)) or "—", pr_marks(entry["prs"]))


def finished_row(entry: dict) -> tuple[str, ...]:
    return (link(entry["title"], entry["url"]), f"{STATE_MARKS[entry['state']]} {entry['state']}", cell(entry["now"]) or "—", when(entry["updated"]))


def counts_table(counts: dict) -> Table:
    names = (*OPEN_STATES, *FINISHED_STATES)
    columns = ("🙋 Needs you", *(f"{STATE_MARKS[name]} {STATE_TITLES[name]}" for name in names))
    return Table("", columns, [(f"**{counts['queue']}**", *(f"**{counts[name]}**" for name in names))], 1, "")


def projects_table(groups: dict, dashboard: str, shown: int | None) -> Table:
    names = (*OPEN_STATES, *FINISHED_STATES)
    counts = Counter((entry["project"] or "No project", name) for name in names for entry in groups[name])
    projects = sorted(
        {project for project, _ in counts},
        key=lambda project: (-sum(counts[project, name] for name in OPEN_STATES), -sum(counts[project, name] for name in names), project),
    )
    rows = [(f"**{cell(project, NAME_CHARS)}**", *(str(counts[project, name] or "·") for name in names)) for project in projects]
    return Table("📊 Every project at a glance", ("Project", *(STATE_MARKS[name] for name in names)), rows[:shown], len(rows), f"{dashboard}#/now")


def table_of(heading: str, columns: tuple[str, ...], row: Callable[[dict], tuple[str, ...]], items: list[dict], rest: str, shown: int | None) -> Table:
    return Table(f"{heading} ({len(items)})", columns, [row(item) for item in items[:shown]], len(items), rest)


def layout(model: dict, dashboard: str, shown: int | None = ROWS) -> list[Table]:
    """The status page as typed rows: a strip counting what waits on him and each state, then a table for each part of his queue, every project's count by state, each open state's features and finished work, each at most `shown` rows, all of them when None, with its total, so `rest` on the dashboard holds the others. A table with no rows is left out."""
    groups = {group["state"]: group["features"] for group in model["status"]}
    queue = {kind: [item for item in model["queue"] if item["kind"] == kind] for kind in ("question", "tap", "late")}
    finished = sorted((entry for name in FINISHED_STATES for entry in groups[name]), key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    tables = [
        counts_table(model["counts"]),
        table_of("❓ Your questions", ("Question", "Holds up", "Asked", "Recommended"), question_row, queue["question"], f"{dashboard}#/queue/question", shown),
        table_of("👆 PRs waiting on your tap", ("Pull request", "Checks", "Card since"), tap_row, queue["tap"], f"{dashboard}#/queue/tap", shown),
        table_of("⏰ Promised times that passed", ("Order", "Promised", "Now"), late_row, queue["late"], f"{dashboard}#/queue/all", shown),
        projects_table(groups, dashboard, shown),
        *(table_of(f"{STATE_MARKS[name]} {STATE_TITLES[name]}", ("Feature", "Project", "Now", "PRs"), feature_row, groups[name], f"{dashboard}#/now", shown) for name in OPEN_STATES),
        table_of("✅ Finished", ("Feature", "State", "Now", "When"), finished_row, finished, f"{dashboard}#/history", shown),
    ]
    return [table for table in tables if table.rows]


def table_lines(table: Table) -> list[str]:
    head = [f"## {table.heading}", ""] if table.heading else []
    rows = ["| " + " | ".join(row) + " |" for row in [table.columns, *table.rows]]
    more = ["", f"*{table.total - len(table.rows)} more on the [dashboard]({table.rest})*"] if table.total > len(table.rows) else []
    return ["", *head, rows[0], "|" + "---|" * len(table.columns), *rows[1:], *more]


def render(model: dict, dashboard: str, shown: int | None = ROWS) -> str:
    """The Slack status page of a drive's model, which `drive view` prints too: its `layout` of at most `shown` rows a table drawn as native markdown tables under an Updated line linking the dashboard."""
    lines = [f"Updated {when(model['at'])} · [open the dashboard]({dashboard})"]
    if model.get("cards", "live") != "live":
        lines += ["", f"Your Slack questions are not listed here: cc-slack {cut(model['cards'], CELL_CHARS)}"]
    for table in layout(model, dashboard, shown):
        lines += table_lines(table)
    return "\n".join(lines) + "\n"


def draw(name: str, markdown: str) -> Drawn:
    """Draw the page on the drive's canvas, creating it as a tab in his DM the first time, and again when the stored canvas is gone or Slack calls it full; returns the page's URL and whether it moved to a new tab."""
    canvas = state.load(name)["slack"].get("page")
    argv = ["canvas", "--title", f"{name}: where it stands"]
    if canvas:
        argv += ["--canvas", canvas]
    try:
        drawn = slack.cc_slack(*argv, timeout=60, stdin=markdown)
    except slack.SlackError as error:
        refused = REFUSAL.search(str(error))
        raise PageError(f"Slack refused the redraw ({refused[1]})" if refused else str(error)) from None
    if drawn["canvas_id"] != canvas:
        with state.edit(name) as data:
            data["slack"]["page"] = drawn["canvas_id"]
    return Drawn(drawn["url"], bool(canvas) and drawn["canvas_id"] != canvas)
