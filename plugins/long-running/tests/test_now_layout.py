import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app"
MODEL = Path(__file__).resolve().parent / "fixtures" / "now-model.json"
LAPTOP = {"width": 1440, "height": 900}
STORY = {"type": "saved", "status": None, "draft": None, "lines": [{"type": "line", "at": "7:20 PM", "mark": "progress", "line": "Three files still use the deleted store; the reader change removes them."}, {"type": "line", "at": "6:02 PM", "mark": "finding", "line": "The build fails in 41 packages."}]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
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
        page.wait_for_selector(".nw-cols")
        yield page
        browser.close()
    server.shutdown()


def now(page):
    page.goto(page.url.split("#")[0] + "#/")
    page.wait_for_selector(".nw-cols")


def test_status_progress_blockers_and_work_fit_a_laptop_screen(page):
    now(page)
    for selector in (".nw-status", ".nw-read", ".nw-journey", ".nw-chart", ".nw-work"):
        box = page.locator(selector).bounding_box()
        assert box["y"] + box["height"] <= LAPTOP["height"], selector


def test_every_open_blocker_and_work_row_says_how_long_it_has_waited(page):
    now(page)
    rows = page.locator(".nw-path .nd:not(.goal), .nw-work .wr").all_inner_texts()
    assert rows
    assert [row for row in rows if not re.search(r"for \d+ (min|h)", row) or not re.search(r"since \d{1,2}:\d{2} [AP]M", row)] == []


def test_the_needs_you_band_shows_every_word(page):
    now(page)
    cut = page.evaluate("""() => [...document.querySelectorAll('.nw-band .what, .nw-band .sub, .nw-band .nbtn')]
        .filter((el) => el.scrollWidth > el.clientWidth + 1 || getComputedStyle(el).textOverflow === 'ellipsis')
        .map((el) => el.className)""")
    assert page.locator(".nw-band .nl").count() >= 2
    assert cut == []


def test_the_needs_you_sheet_frames_each_question_once(page):
    page.goto(page.url.split("#")[0] + "#/now")
    page.wait_for_selector(".bcard")
    nested = page.evaluate("""() => {
        const framed = (el) => el.matches('section, article, div') && parseFloat(getComputedStyle(el).borderTopWidth) > 0 && parseFloat(getComputedStyle(el).borderTopLeftRadius) > 0;
        return [...document.querySelectorAll('.bcard *')].filter((el) => {
          if (!framed(el)) return false;
          let depth = 0;
          for (let up = el.parentElement; up && !up.classList.contains('sheet'); up = up.parentElement) if (framed(up)) depth += 1;
          return depth > 1;
        }).map((el) => el.className);
    }""")
    assert nested == []


def test_needs_you_headings_are_one_neutral_color(page):
    page.goto(page.url.split("#")[0] + "#/now")
    page.wait_for_selector(".bcard")
    loud = page.evaluate("""() => [...document.querySelectorAll('.bcard > header h3')]
        .filter((h) => h.querySelector('.nmark, a, i') || [...h.querySelectorAll('*')].some((el) => getComputedStyle(el).color !== getComputedStyle(h).color))
        .map((h) => h.textContent)""")
    assert page.locator(".bcard > header .nmark").count() == 0
    assert loud == []


def test_a_storys_now_line_is_its_newest_update(page):
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    page.goto(page.url.split("#")[0] + f"#/story/{item}")
    page.wait_for_selector(".nw-storyhead .nw-sub")
    assert "Three files still use the deleted store" in page.locator(".nw-storyhead .now").inner_text()
