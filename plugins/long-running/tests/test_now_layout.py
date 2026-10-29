import datetime as dt
import json
import re
import threading
from zoneinfo import ZoneInfo
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app"
MODEL = Path(__file__).resolve().parent / "fixtures" / "now-model.json"
ASSIST = APP.parents[1] / "skills" / "long-running" / "templates" / "drive-assist.html"
LAPTOP = {"width": 1440, "height": 900}
PACIFIC_NOW = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
STORY = {"type": "saved", "status": None, "draft": None, "lines": [{"type": "line", "at": f"{(PACIFIC_NOW - dt.timedelta(minutes=5)):%-I:%M %p}", "mark": "progress", "line": "Three files still use the deleted store; the reader change removes them."}, {"type": "line", "at": f"{(PACIFIC_NOW - dt.timedelta(minutes=80)):%-I:%M %p}", "mark": "finding", "line": "The build fails in 41 packages."}]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self.reply((APP / "index.html").read_text().replace("</body>", ASSIST.read_text() + "</body>").encode(), "text/html")
        if path == "/api/model":
            return self.reply(MODEL.read_bytes(), "application/json")
        if path.startswith("/api/story/"):
            return self.reply(f"data: {json.dumps(STORY)}\n\n".encode(), "text/event-stream")
        if path.startswith("/api/"):
            return self.reply(b"", "text/event-stream")
        return super().do_GET()

    def reply(self, body, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def page():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport=LAPTOP)
        page.goto(f"http://127.0.0.1:{server.server_port}/#/")
        page.wait_for_selector(".nw-spine")
        yield page
        browser.close()
    server.shutdown()


def now(page):
    page.goto(page.url.split("#")[0] + "#/")
    page.wait_for_selector(".nw-spine")


def test_the_finished_product_picture_sits_above_the_milestones(page):
    now(page)
    assert page.locator(".nw-diagram").bounding_box()["y"] < page.locator(".nw-spine").bounding_box()["y"]


def test_every_open_blocker_and_the_focused_work_say_how_long_they_have_waited(page):
    now(page)
    rows = page.locator(".nw-slot .pan.focus :is(.chain .hc, .feed .lead)").all_inner_texts()
    assert rows
    assert [row for row in rows if not re.search(r"for \d+ (min|h)", row) or not re.search(r"since \d{1,2}:\d{2} [AP]M", row)] == []


def test_a_milestone_holding_a_blocker_on_you_is_marked(page):
    now(page)
    brief = json.loads(MODEL.read_text())["brief"]
    held = sorted({p["milestone"] for p in brief["paths"] if any(n["state"] == "you" for n in p["nodes"])})
    assert held
    marked = page.locator(".nw-spine .sp:has(.need)").evaluate_all("(cols) => cols.map((c) => Number(c.dataset.i))")
    assert marked == held


def rest_on(page, selector):
    page.locator(selector).scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    box = page.locator(selector).bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=4)
    page.wait_for_timeout(450)


def focused(page):
    return int(page.locator(".nw-slot .pan.focus").get_attribute("data-i"))


def test_resting_on_a_milestone_opens_it_without_moving_the_strip(page):
    now(page)
    cur = int(page.locator(".nw-slot").get_attribute("data-cur"))
    other = 0 if cur else 1
    before = page.locator(".nw-spine a.ms").evaluate_all("(links) => links.map((a) => a.getBoundingClientRect().left)")
    rest_on(page, f'.nw-spine .sp[data-i="{other}"] .ms')
    assert focused(page) == other
    assert page.locator(".nw-spine a.ms").evaluate_all("(links) => links.map((a) => a.getBoundingClientRect().left)") == before
    page.mouse.move(5, 5, steps=4)
    page.wait_for_timeout(700)
    assert focused(page) == cur


def test_scrolling_under_a_resting_pointer_keeps_the_open_milestone(page):
    now(page)
    cur = int(page.locator(".nw-slot").get_attribute("data-cur"))
    rest_on(page, f'.nw-spine .sp[data-i="{cur}"] .ms')
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(600)
    assert focused(page) == cur


def test_the_needs_you_band_shows_every_word(page):
    now(page)
    cut = page.evaluate("""() => [...document.querySelectorAll('.nw-band .what, .nw-band .sub, .nw-band .nbtn')]
        .filter((el) => el.scrollWidth > el.clientWidth + 1 || getComputedStyle(el).textOverflow === 'ellipsis')
        .map((el) => el.className)""")
    assert page.locator(".nw-band .nl").count() >= 1
    assert cut == []


def test_review_frames_each_question_once(page):
    page.goto(page.url.split("#")[0] + "#/review")
    page.wait_for_selector(".bq")
    nested = page.evaluate("""() => {
        const framed = (el) => el.matches('section, article, div') && parseFloat(getComputedStyle(el).borderTopWidth) > 0 && parseFloat(getComputedStyle(el).borderTopLeftRadius) > 0;
        return [...document.querySelectorAll('.bq *')].filter((el) => {
          if (!framed(el)) return false;
          let depth = 0;
          for (let up = el.parentElement; up; up = up.parentElement) if (framed(up)) depth += 1;
          return depth > 1;
        }).map((el) => el.className);
    }""")
    assert nested == []


def test_review_question_headings_are_one_neutral_color(page):
    page.goto(page.url.split("#")[0] + "#/review")
    page.wait_for_selector(".bq")
    loud = page.evaluate("""() => [...document.querySelectorAll('.bq > header .mr-t')]
        .filter((h) => h.querySelector('.nmark, a, i') || [...h.querySelectorAll('*')].some((el) => getComputedStyle(el).color !== getComputedStyle(h).color))
        .map((h) => h.textContent)""")
    assert loud == []


def test_a_storys_now_line_is_its_newest_update(page):
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    page.goto(page.url.split("#")[0] + f"#/story/{item}")
    page.wait_for_selector(".nw-storyhead .nw-sub")
    assert "Three files still use the deleted store" in page.locator(".nw-storyhead .now").inner_text()


def test_every_milestone_opens_its_own_panel(page):
    now(page)
    segs = page.locator(".nw-journey a.ms").evaluate_all("(links) => links.map((a) => a.dataset.seg)")
    assert segs == [f"plan/m{i}" for i in range(len(json.loads(MODEL.read_text())["milestones"]))]


def test_progress_says_how_many_milestones_are_live(page):
    now(page)
    model = json.loads(MODEL.read_text())
    live = sum(stop["state"] == "live" for stop in model["brief"]["progress"]["stops"])
    assert page.locator(".nw-sent").inner_text().startswith(f"{live} of {len(model['milestones'])} milestones are live.")


def test_a_story_links_to_its_full_transcript(page):
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    page.goto(page.url.split("#")[0] + f"#/story/{item}")
    page.wait_for_selector(".nw-raw")
    assert page.locator(".nw-raw").get_attribute("href") == f"/api/transcript/{item}"
