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
    live = sum(stop["state"] == "live" for stop in model["milestone_state"])
    assert page.locator(".nw-sent").inner_text().startswith(f"{live} of {len(model['milestones'])} milestones are live.")


def test_a_story_links_to_its_full_transcript(page):
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    page.goto(page.url.split("#")[0] + f"#/story/{item}")
    page.wait_for_selector(".nw-raw")
    assert page.locator(".nw-raw").get_attribute("href") == f"/api/transcript/{item}"


def test_a_question_opens_in_a_panel_with_its_answers(page):
    card = next(item["card"] for item in json.loads(MODEL.read_text())["needs"] if item["kind"] == "card")
    page.goto(page.url.split("#")[0] + f"#/~question/{card['id']}")
    page.wait_for_selector(".pr-drawer.front .bq")
    assert page.locator(".pr-drawer.front [data-choose]").count() == len(card["options"])


def test_the_milestones_sit_between_the_picture_and_the_live_feed(page):
    now(page)
    assert page.locator(".nw-diagram").bounding_box()["y"] < page.locator(".nw-spine").bounding_box()["y"] < page.locator(".nw-live").bounding_box()["y"]


def test_tapping_a_worker_swaps_the_live_feed_to_its_story_and_back(page):
    now(page)
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    page.locator(f'.nw-live .wr[data-feed="{item}"]').click()
    page.wait_for_selector(".nw-live .nw-storyhead .nw-sub")
    assert "Three files still use the deleted store" in page.locator(".nw-live .nw-storyhead .now").inner_text()
    page.locator(".nw-live [data-feed-all]").click()
    page.wait_for_selector(".nw-live .nw-work")


def test_a_row_shows_its_newest_event_and_its_panel_the_whole_history(page):
    now(page)
    model = json.loads(MODEL.read_text())
    key, entry = next(iter(model["history"].items()))
    item = next(w["item"] for w in model["brief"]["work"] if key.removeprefix("lane:") in (w.get("workers") or []))
    assert entry["events"][0]["text"] in page.locator(f'.nw-live .wr[data-feed="{item}"] .ev').text_content()
    page.goto(page.url.split("#")[0] + f"#/~work/{item}")
    page.wait_for_selector(".pr-drawer.front .hist")
    shown = page.locator(".pr-drawer.front .hist .evs li").all_inner_texts()
    assert len(shown) == len(entry["events"])
    assert all(line.endswith(e["text"]) for line, e in zip(shown, entry["events"]))
    assert entry["events"][0]["holder"] in page.locator(".pr-drawer.front .hist .pg-facts").inner_text()


def test_every_blocked_milestone_and_blocker_says_what_unblocks_it(page):
    now(page)
    blocked = page.locator(".nw-spine .sp:is(.ms-paused, .ms-broken)")
    assert blocked.count() >= 1
    assert [t[:12] for t in blocked.locator(".unb").all_text_contents()] == ["Unblocking: "] * blocked.count()
    rows = page.locator(".nw-slot .pan.focus .chain .hc:not(.you)")
    assert [t[:12] for t in rows.locator(".unb").all_inner_texts()] == ["Unblocking: "] * rows.count()


def test_the_milestones_show_the_state_computed_from_records_not_a_stale_summary(page):
    now(page)
    computed = json.loads(MODEL.read_text())["milestone_state"]
    spine = page.locator(".nw-spine .sp")
    assert [re.search(r"\bms-(\w+)", c)[1] for c in spine.evaluate_all("(els) => els.map((e) => e.className)")] == [m["state"] for m in computed]
    assert page.locator(".nw-spine .sp .st").all_inner_texts() == [m["label"] for m in computed]
    assert page.locator(".nw-spine .sp .unb").all_text_contents() == [f"Unblocking: {m['unblocking']}" for m in computed if m["blockers"]]


def test_a_blocked_milestone_says_its_state_its_blocker_and_its_unblocking_once_each(page):
    now(page)
    model = json.loads(MODEL.read_text())
    i, held = next((i, m) for i, m in enumerate(model["milestone_state"]) if m["blockers"])
    block = page.locator(f'.nw-spine .sp[data-i="{i}"]').text_content()
    assert block.count(held["label"]) == 1
    assert block.count(held["waits"]) == 1
    assert block.count("Unblocking:") == 1 and held["unblocking"] in block
    assert re.search(r"for \d+ (min|h|hours), since", block)
    nodes = next(p for p in model["brief"]["paths"] if p["milestone"] == i)["nodes"]
    shown = page.locator(f'.nw-slot .pan[data-i="{i}"] .chain .unb').all_text_contents()
    assert shown == [f"Unblocking: {n['how']}" for n in nodes if n["state"] != "you"]


def test_blockers_and_work_share_the_full_width_under_the_milestones(page):
    now(page)
    slot = page.locator(".nw-slot").bounding_box()
    pan = page.locator(".nw-slot .pan.focus").bounding_box()
    assert pan["width"] > slot["width"] * 0.95
    cols = [page.locator(".nw-slot .pan.focus .two > div").nth(i).bounding_box() for i in range(page.locator(".nw-slot .pan.focus .two > div").count())]
    assert len(cols) == 2
    assert cols[1]["x"] >= cols[0]["x"] + cols[0]["width"] and abs(cols[1]["y"] - cols[0]["y"]) < 2


def test_only_a_new_event_animates(page):
    now(page)
    assert page.locator(".ev.fresh").count() == 0
    page.evaluate("() => render()")
    assert page.locator(".ev.fresh").count() == 0
    page.evaluate("""() => {
        const h = Object.values(state.model.history)[0];
        h.events.unshift({ ...h.events[0], ts: new Date().toISOString(), text: 'Re-graded clean' });
        render();
    }""")
    assert page.locator(".ev.fresh").count() >= 1
    page.evaluate("() => render()")
    assert page.locator(".ev.fresh").count() == 0


def test_tab_never_leaves_an_open_panel(page):
    page.goto(page.url.split("#")[0] + "#/~plan/m0")
    page.wait_for_selector(".pr-drawer.front")
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate("() => Boolean(document.activeElement.closest('.pr-drawer.front'))")
    for _ in range(4):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => Boolean(document.activeElement.closest('.pr-drawer.front'))")


def test_a_milestone_panel_links_to_its_own_page(page):
    page.goto(page.url.split("#")[0] + "#/~plan/m0")
    page.wait_for_selector(".pr-drawer.front")
    page.locator('.pr-drawer.front a[href="#/milestone/0"]').click()
    page.wait_for_function("() => location.hash === '#/milestone/0' && !document.querySelector('.pr-drawer')")
    assert json.loads(MODEL.read_text())["milestones"][0] in page.locator("#main .pg").inner_text()


def test_back_then_tab_keeps_the_reading_position(page):
    page.set_viewport_size({"width": 1440, "height": 420})
    now(page)
    page.locator("#main a[data-seg]").first.click()
    page.wait_for_selector(".pr-drawer.front")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('.pr-drawer')")
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    y = page.evaluate("() => window.scrollY")
    assert y > 300
    page.locator('#tabs a[href="#/plan"]').click()
    page.wait_for_function("() => location.hash === '#/plan'")
    page.go_back()
    page.wait_for_function("() => location.hash === '#/'")
    page.keyboard.press("Tab")
    after = page.evaluate("() => window.scrollY")
    page.set_viewport_size(LAPTOP)
    assert after > y - 420


def test_a_work_row_reads_its_pull_requests_runner_state_over_the_summary(page):
    now(page)
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    page.evaluate("""(item) => {
        state.model.brief.work.find((w) => w.item === item).changes = [42742];
        state.model.pr_state = { 42742: { state: 'Landed', since: new Date().toISOString(), line: 'Landed at 6:09 PM' } };
        render();
    }""", item)
    row = page.locator(f'.nw-live .wr[data-feed="{item}"]')
    assert row.locator(".chip").text_content() == "Landed"
    assert "Landed at 6:09 PM" in row.inner_text()


def test_an_incident_never_hides_what_waits_on_him(page):
    now(page)
    page.evaluate("() => { state.model.brief.band = { kind: 'incident', text: 'The Mac app crashes' }; render(); }")
    assert page.locator(".nw-band.incident").is_visible()
    assert page.locator(".nw-band.nw-needs").is_visible()


def test_the_feed_shows_the_focused_work_first_and_other_works_latest_line_only_on_hover(page):
    now(page)
    rows = page.locator(".nw-live .wr")
    assert "lead" in rows.first.get_attribute("class")
    rest = page.locator(".nw-live .wr.rest:not(.st-ready-for-you):has(.ev)").first
    assert not rest.locator(".ev").is_visible()
    rest.hover()
    assert rest.locator(".ev").is_visible()


def test_the_prevention_table_lists_open_ones_first_red_only_without_a_prevention_and_folds_live_ones(page):
    now(page)
    model = json.loads(MODEL.read_text())
    rows = page.locator(".nw-prevent > .pt .pt-row")
    order = ["none", "building", "open", "merged", "live"]
    in_flight = sorted((p for p in model["preventions"] if p["state"] != "live"), key=lambda p: order.index(p["state"]))
    assert [t.split("\n")[0] for t in rows.all_inner_texts()] == [p["failure"] for p in in_flight]
    assert [r.get_attribute("class").split() for r in rows.all()] == [["pt-row", p["state"]] for p in in_flight]
    assert page.locator(".nw-prevent .pt-row.none .chip").all_inner_texts() == ["No prevention yet"] * sum(p["state"] == "none" for p in model["preventions"])
    assert not page.locator(".nw-prevent details .pt-row").first.is_visible()


def spine_widths(page):
    return page.locator(".nw-spine .sp").evaluate_all("(els) => els.map((e) => e.getBoundingClientRect().width)")


def test_the_focused_milestone_is_widest_and_alone_shows_its_blocker_and_next_step(page):
    now(page)
    widths = spine_widths(page)
    cur = int(page.locator(".nw-spine .sp.focus").get_attribute("data-i"))
    assert widths[cur] > 2 * max(w for i, w in enumerate(widths) if i != cur)
    i = next(i for i, m in enumerate(json.loads(MODEL.read_text())["milestone_state"]) if m["blockers"])
    blocked = page.locator(f'.nw-spine .sp[data-i="{i}"]')
    assert not blocked.locator(".why").is_visible()
    blocked.hover()
    page.wait_for_timeout(700)
    assert blocked.locator(".why").is_visible() and blocked.locator(".unb").is_visible()
    widths = spine_widths(page)
    assert widths[i] > 2 * max(w for j, w in enumerate(widths) if j != i)
    page.mouse.move(0, 0)
    page.wait_for_timeout(900)
