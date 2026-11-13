"""Pages: one-off live dashboards a page job builds for an order, each a folder under the drive's state dir that the dashboard serves in a sandboxed frame and feeds its slice of the board."""

import json
import re
import shutil
from pathlib import Path

from drive import state
from drive.state import DriveError

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
KINDS = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def pages_dir(name: str) -> Path:
    return state.drive_dir(name) / "pages"


def add(name: str, slug: str, order_id: str, folder: Path) -> dict:
    """Check the folder is a page the dashboard can serve for the order and put it at pages/<slug>, replacing the page of that slug whole: its index.html, a page.json naming its title and goal, and the orders and PRs it shows, all known to the drive, and only files of KINDS."""
    if not SLUG.fullmatch(slug):
        raise DriveError(f"{slug!r} is no page slug: lowercase letters, digits and dashes, starting with a letter or digit, at most 63")
    drive = state.load(name)
    if order_id not in drive["orders"]:
        raise DriveError(f"the {name} drive has no order {order_id}")
    if not (folder / "index.html").is_file():
        raise DriveError(f"{folder} holds no index.html, the page the dashboard opens")
    said = json.loads((folder / "page.json").read_text()) if (folder / "page.json").is_file() else {}
    if not said.get("title") or not said.get("goal"):
        raise DriveError(f"{folder}/page.json names no title or no goal: both are what Pages lists the page by")
    files = [path for path in folder.rglob("*") if path.is_file()]
    if strange := sorted(str(path.relative_to(folder)) for path in files if path.suffix not in KINDS):
        raise DriveError(f"the dashboard serves only {', '.join(KINDS)} files from a page, not {', '.join(strange)}")
    orders = list(dict.fromkeys([order_id, *said.get("orders", [])]))
    if unknown := [each for each in orders if each not in drive["orders"]] + [each for each in said.get("prs", []) if each not in drive["prs"]]:
        raise DriveError(f"page.json names {', '.join(unknown)}, which the {name} drive does not hold")
    order = drive["orders"][order_id]
    page = {
        "title": said["title"],
        "goal": said["goal"],
        "orders": orders,
        "prs": said.get("prs", []),
        "project": order["project"],
        "workstream": order["workstream"],
        "source": said.get("source"),
        "job": said.get("job"),
        "created": state.stamp(),
    }
    target = pages_dir(name) / slug
    staged, old = target.with_name(f".{slug}.new"), target.with_name(f".{slug}.old")
    for left in (staged, old):
        shutil.rmtree(left, ignore_errors=True)
    shutil.copytree(folder, staged)
    (staged / "page.json").write_text(json.dumps(page, indent=2) + "\n")
    if target.exists():
        target.rename(old)
    staged.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    return {"slug": slug, **page}


def listed(name: str) -> list[dict]:
    """Every page the drive holds, newest first."""
    found = [{"slug": path.name, **json.loads((path / "page.json").read_text())} for path in pages_dir(name).glob("*/") if SLUG.fullmatch(path.name)]
    return sorted(found, key=lambda page: state.parse_time(page["created"]), reverse=True)


def shown(drive: dict, pages: list[dict]) -> list[dict]:
    """Each page as Pages lists it: live while any order it shows is open, with its first order's title."""
    return [
        page | {"live": any(drive["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES for order_id in page["orders"]), "order_title": drive["orders"][page["orders"][0]]["title"]}
        for page in pages
    ]


def file(name: str, slug: str, path: str) -> Path | None:
    """The file a page asks for by its path under the page, index.html for its own path, or None for one outside it or of a kind not served."""
    if not SLUG.fullmatch(slug):
        return None
    home = (pages_dir(name) / slug).resolve()
    found = (home / (path or "index.html")).resolve()
    return found if found.is_relative_to(home) and found.suffix in KINDS and found.is_file() else None
