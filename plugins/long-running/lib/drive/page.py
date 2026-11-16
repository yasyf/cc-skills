import hashlib
import re
from collections import Counter
from collections.abc import Callable
from typing import NamedTuple
from urllib.parse import urlencode, urlparse

from drive import slack, state
from drive.render import cut, when

ROWS = 8
CANVAS_ORDERS = 40
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
    new: bool


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
    counted = [("more open", len(marks) - PRS_SHOWN), *((word, sum(pr["state"] == word for pr in prs)) for word in ("merged", "folded", "closed"))]
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


def built_words(built: dict) -> str:
    return f"🟣 built on `{built['branch']}`, {'no PR yet' if built['waits'] else 'its PR opens next'}"


def feature_row(entry: dict) -> tuple[str, ...]:
    cards = (f"❓ waits on {link(card['title'], card['url'], NAME_CHARS)}" for card in entry["cards"])
    now = [cell(entry["now"]), *map(built_words, entry["built"]), *cards]
    return (link(entry["title"], entry["url"]), cell(entry["project"] or "—", NAME_CHARS), "<br>".join(filter(None, now)) or "—", pr_marks(entry["prs"]))


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
    queue = {kind: [item for item in model["queue"] if item["kind"] == kind] for kind in ("question", "tap")}
    finished = sorted((entry for name in FINISHED_STATES for entry in groups[name]), key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    tables = [
        counts_table(model["counts"]),
        table_of("❓ Your questions", ("Question", "Holds up", "Asked", "Recommended"), question_row, queue["question"], f"{dashboard}#/queue/question", shown),
        table_of("👆 PRs waiting on your tap", ("Pull request", "Checks", "Card since"), tap_row, queue["tap"], f"{dashboard}#/queue/tap", shown),
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
    """The status page of a drive's model, which `drive view` prints: its `layout` of at most `shown` rows a table drawn as native markdown tables under an Updated line linking the dashboard."""
    lines = [f"Updated {when(model['at'])} · [open the dashboard]({dashboard})"]
    if model.get("cards", "live") != "live":
        lines += ["", f"Your Slack questions are not listed here: cc-slack {cut(model['cards'], CELL_CHARS)}"]
    for table in layout(model, dashboard, shown):
        lines += table_lines(table)
    return "\n".join(lines) + "\n"


def channel_link(made: dict) -> str:
    return f"[#{made['name']}](https://{slack.workspace()}/archives/{made['id']})"


def channel_line(made: dict, count: int, queued: int, queue: str) -> str:
    return f"- {channel_link(made)} · {count} open · {queued} waiting on you · [dashboard]({queue})"


def order_line(entry: dict, dashboard: str) -> str:
    cards = "".join(f" · ❓ {link(card['title'], card['url'], NAME_CHARS)}" for card in entry["cards"])
    title = link(entry["id"] + ": " + entry["title"], f"{dashboard}#/feature/{entry['id']}")
    return f"- {STATE_MARKS[entry['state']]} {title} · {entry['state']}{cards}"


def waiting_line(item: dict) -> str:
    return f"- {'👆' if item['kind'] == 'tap' else '❓'} {link(item['title'], item['url'])}"


def seat_query(dashboard: str, data: dict, seat: str) -> str:
    """Where a seat's channel links on the dashboard: a priority seat's page of the orders it spans, by his words "prio 'projects' should be different on the dashboard than regular projects", else its project's queue."""
    held = next((order_id for order_id, order in data["orders"].items() if order["priority"] == seat), None)
    return f"{dashboard}#/priority/{held}" if held else f"{dashboard}#/queue/all?{urlencode({'project': seat})}"


def canvases(name: str, data: dict, model: dict, dashboard: str) -> dict[str, tuple[str, str]]:
    """The title and words of each channel's own canvas, by its seat in the drive's slack row, each a bird's-eye view linking into the dashboard: a seat's channel its open orders, one line each with its state and the cards it waits on, then what waits on him there; the drive's own channel one line per other channel, linking it and its queue on the dashboard with how many orders are open and how many cards wait on him there, then the orders and cards filed in no channel of their own. No time is drawn, so the words change only when what they show does."""
    from drive import talk

    kept = data["slack"]["channels"]
    seats = {made["id"]: seat for seat, made in kept.items()}
    orders = {channel: [] for channel in seats}
    waiting = {channel: [] for channel in seats}
    for group in model["status"]:
        for entry in group["features"] if group["state"] in OPEN_STATES else []:
            orders.setdefault(slack.project_channel(data, state.channel_seat_of(data, data["orders"][entry["id"]])), []).append(entry)
    for item in model["queue"]:
        waiting.setdefault(talk.queue_channel(data, item), []).append(item)
    drawn = {}
    for channel, seat in seats.items():
        held = state.seat_order(data, seat)
        shown = (held["title"] or state.whole_order(held["text"])) if held else seat or name
        lines = [f"# {shown}: where it stands", "", f"[Open the dashboard]({seat_query(dashboard, data, seat) if seat else dashboard})"]
        if held and held["terms"]:
            lines += ["", "## What each name means", *(f"- **{word}**: {meaning}" for word, meaning in held["terms"].items())]
        if not seat:
            lines += ["", "## Channels", *(channel_line(made, len(orders[made["id"]]), len(waiting[made["id"]]), seat_query(dashboard, data, other)) for other, made in kept.items() if other)]
        lines += ["", f"## Waiting on you ({len(waiting[channel])})", *(map(waiting_line, waiting[channel][:ROWS]) if waiting[channel] else ["Nothing waits on you here."])]
        if len(waiting[channel]) > ROWS:
            lines.append(f"*{len(waiting[channel]) - ROWS} more on the [dashboard]({seat_query(dashboard, data, seat) if seat else f'{dashboard}#/queue/all'})*")
        lines += ["", f"## Open orders ({len(orders[channel])})", *([order_line(entry, dashboard) for entry in orders[channel][:CANVAS_ORDERS]] or ["No open orders here."])]
        if len(orders[channel]) > CANVAS_ORDERS:
            lines.append(f"*{len(orders[channel]) - CANVAS_ORDERS} more on the [dashboard]({dashboard}#/now)*")
        drawn[seat] = (f"{shown}: where it stands", "\n".join(lines) + "\n")
    return drawn


def releases(data: dict, seat: str) -> list[str]:
    """One line for each release job still at work on an order of the seat, the drive's own taking every one: its order's title, its state and the last line it said."""
    channel = data["slack"]["channels"][seat]["id"]
    return [
        f"- {data['orders'][job['order']]['title'] or cut(data['orders'][job['order']]['text'], TITLE_CHARS)}: release {job['state']}" + (f", last said: {cut(job['said'], CELL_CHARS)}" if job["said"] else "")
        for job in data["jobs"].values()
        if state.starts_cut(job) and job["state"] not in state.FINISHED_JOB_STATES and (not seat or slack.project_channel(data, state.coordinator_seat(data, data["orders"][job["order"]])) == channel)
    ]


def write_briefs(name: str, data: dict, drawn: dict[str, tuple[str, str]]) -> None:
    """Write each seat's brief: its canvas's words, then each release at work there, so the communicator reads where its project stands from one local file with no Slack or CI read."""
    for seat, (_, markdown) in drawn.items():
        path = state.brief_path(name, seat)
        path.parent.mkdir(parents=True, exist_ok=True)
        shipping = releases(data, seat)
        path.write_text(markdown + "\n## Releases at work\n" + ("\n".join(shipping) if shipping else "None.") + "\n")


def draw(name: str, seat: str, title: str, markdown: str) -> Drawn:
    """Draw a channel's own canvas, the seat's in the drive's slack row, through `cc-slack canvas --channel`, which creates it the first time and redraws it after, and keep its id and the digest of its words on that channel's entry; returns its URL and whether it is new."""
    made = state.load(name)["slack"]["channels"][seat]
    argv = ["canvas", "--channel", made["id"], "--title", title, *(("--canvas", made["canvas"]) if made.get("canvas") else ())]
    try:
        drawn = slack.cc_slack(*argv, timeout=60, stdin=markdown)
    except slack.SlackError as error:
        refused = REFUSAL.search(str(error))
        raise PageError(f"Slack refused the redraw ({refused[1]})" if refused else str(error)) from None

    def draw_write(data: dict):
        data["slack"]["channels"][seat] |= {"canvas": drawn["canvas_id"], "canvas_digest": digest(markdown)}

    state.write(name, draw_write, patient=True)
    return Drawn(drawn["url"], drawn["canvas_id"] != made.get("canvas"))


def digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()
