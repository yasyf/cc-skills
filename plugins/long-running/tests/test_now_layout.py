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
    model = MODEL.read_bytes()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            served = (APP / "index.html").read_text().replace("</body>", ASSIST.read_text() + "</body>")
            return self.reply((served.replace("<html", '<html class="ai-on"', 1) if "ai=1" in self.path else served).encode(), "text/html")
        if path == "/api/model":
            return self.reply(self.model, "application/json")
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


def open_first_question(page):
    now(page)
    page.evaluate("location.hash = `#/needs~question/${state.model.needs.find((item) => item.kind === 'card').card.id}`")
    page.wait_for_selector(".bq")


def open_work_row(page, item):
    page.locator(f'.nw-live details[data-id="wr-{item}"] > summary').click()


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


def test_resting_on_a_milestone_opens_and_widens_it_under_the_pointer(page):
    now(page)
    cur = int(page.locator(".nw-slot").get_attribute("data-cur"))
    other = 0 if cur else 1
    rest_on(page, f'.nw-spine .sp[data-i="{other}"] .ms')
    page.wait_for_timeout(300)
    assert focused(page) == other
    widths = spine_widths(page)
    assert widths[other] > 2 * max(w for i, w in enumerate(widths) if i != other)
    box = page.locator(f'.nw-spine .sp[data-i="{other}"] .ms').bounding_box()
    assert page.evaluate(f"document.elementFromPoint({box['x'] + box['width'] / 2}, {box['y'] + 30}).closest('.sp').dataset.i") == str(other)
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
    open_first_question(page)
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
    open_first_question(page)
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


def test_now_reads_milestones_then_the_live_feed_then_the_merge_map(page):
    now(page)
    assert page.locator(".nw-spine").bounding_box()["y"] < page.locator(".nw-live").bounding_box()["y"] < page.locator(".nw-diagram").bounding_box()["y"]


def test_tapping_a_worker_swaps_the_live_feed_to_its_story_and_back(page):
    now(page)
    item = json.loads(MODEL.read_text())["brief"]["work"][0]["item"]
    open_work_row(page, item)
    page.locator(f'.nw-live [data-feed="{item}"]').click()
    page.wait_for_selector(".nw-live .nw-storyhead .nw-sub")
    assert "Three files still use the deleted store" in page.locator(".nw-live .nw-storyhead .now").inner_text()
    page.locator(".nw-live [data-feed-all]").click()
    page.wait_for_selector(".nw-live .ev-rows")


def test_a_works_panel_shows_the_whole_history(page):
    now(page)
    model = json.loads(MODEL.read_text())
    key, entry = next(iter(model["history"].items()))
    item = next(w["item"] for w in model["brief"]["work"] if key.removeprefix("lane:") in (w.get("workers") or []))
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
    assert [sp.locator(".sf dt").all_text_contents().count("Next") for sp in blocked.all()] == [1] * blocked.count()
    rows = page.locator(".nw-slot .pan.focus .chain .hc:not(.you)")
    assert [t[:12] for t in rows.locator(".unb").all_inner_texts()] == ["Unblocking: "] * rows.count()


def test_the_milestones_show_the_state_computed_from_records_not_a_stale_summary(page):
    now(page)
    computed = json.loads(MODEL.read_text())["milestone_state"]
    spine = page.locator(".nw-spine .sp")
    assert [re.search(r"\bms-(\w+)", c)[1] for c in spine.evaluate_all("(els) => els.map((e) => e.className)")] == [m["state"] for m in computed]
    assert page.locator(".nw-spine .sp .st").evaluate_all("(els) => els.map((e) => e.firstChild.textContent)") == [m["label"] for m in computed]
    fields = page.locator(".nw-spine .sp").evaluate_all("(els) => els.map((e) => Object.fromEntries([...e.querySelectorAll('.sf dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent])))")
    assert [f.get("Next", "") for f in fields] == [m["next"] if m["state"] != "live" else "" for m in computed]


def test_a_blocked_milestone_is_a_list_of_short_fields_not_a_paragraph(page):
    now(page)
    model = json.loads(MODEL.read_text())
    i, held = next((i, m) for i, m in enumerate(model["milestone_state"]) if m["blockers"])
    sp = page.locator(f'.nw-spine .sp[data-i="{i}"]')
    assert re.fullmatch(rf"{held['label']} for \d+ (min|h|hours).*", sp.locator(".st").text_content())
    fields = dict(zip(sp.locator(".sf dt").all_text_contents(), sp.locator(".sf dd").all_text_contents()))
    assert list(fields) == ["Blocked by", "Next", "Since", "Why it matters"]
    assert (fields["Blocked by"], fields["Next"], fields["Why it matters"]) == (held["waits"], held["next"], held["why"])
    assert all(len(value.split()) <= 12 for value in fields.values())
    assert sp.locator(".unb, .why").count() == 0
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
    row = page.locator(f'.nw-live details[data-id="wr-{item}"]')
    assert "Landed" in row.locator("summary").inner_text()
    assert "Landed at 6:09 PM" in row.text_content()


def test_an_incident_never_hides_what_waits_on_him(page):
    now(page)
    page.evaluate("() => { state.model.brief.band = { kind: 'incident', text: 'The Mac app crashes' }; render(); }")
    assert page.locator(".nw-band.incident").is_visible()
    assert page.locator(".nw-band.nw-needs").is_visible()


def test_the_feed_puts_stuck_work_first_and_each_line_opens_to_its_detail(page):
    page.reload()
    now(page)
    rows = page.locator(".nw-live .ev-rows > li")
    assert "Stuck" in rows.first.locator("summary").inner_text()
    assert not rows.first.locator(".ev-more").is_visible()
    rows.first.locator("summary").click()
    assert rows.first.locator(".ev-more").is_visible()
    rows.first.locator("summary").click()


def test_what_failed_folds_to_lines_and_a_broken_row_says_what_it_lacks(page):
    now(page)
    rows = page.locator(".nw-prevent .ev-rows > li")
    assert rows.count() == 4
    broken = rows.filter(has_text="Broken row")
    assert "no mechanism; no state" in broken.text_content()
    assert not broken.locator(".ev-more").is_visible()
    assert "undefined" not in page.locator(".nw-prevent").inner_text().lower()
    assert page.locator(".nw-prevent .nw-h .pg-n").text_content() == "3 not live"
    assert page.locator(".nw-notlive").count() == 0


def test_what_the_coordinator_owes_is_on_chat_folded_by_kind_and_not_on_now(page):
    now(page)
    page.evaluate("""() => {
        state.model.coordinator_overdue = [
            { id: 'ask-1', group: 'ask', due: '3:14 AM', text: 'Cut the job', holder: 'ci-speed-1' },
            { id: 'retro-2', group: 'retro', due: 'Nov 4, 4:20 AM', text: 'The queue dropped changes', holder: 'meta-desk' },
            { id: 'retro-3', group: 'retro', due: 'Nov 4, 4:21 AM', text: 'A lane lost its brief', holder: 'meta-desk' },
        ];
        state.model.owner_asks = { messages: [], taps: [], store: [] };
        state.model.replies = [];
        state.model.chat_lines = [];
        state.model.board.answered = {};
        render();
    }""")
    assert "overdue item" not in page.locator(".nw").inner_text().lower()
    page.evaluate("location.hash = '#/replies'")
    page.locator(".ct-owed > summary").click()
    rows = page.locator(".ct-owed .ev-rows > li")
    assert [row.locator("summary").inner_text().split("\n")[-1] for row in rows.all()][:2] == ["1 ask still open", "2 retro actions past due"]
    assert "A lane lost its brief" in rows.nth(1).text_content()
    assert page.locator(".tab.on .count").count() == 0
    page.evaluate("() => { state.model.coordinator_overdue = []; delete state.model.owner_asks; render(); }")
    now(page)


def test_a_retro_table_that_does_not_read_says_so_above_the_table(page):
    model = json.loads(MODEL.read_text())
    model["errors"] = {"preventions": "the retro table did not read, so this section is incomplete (CalledProcessError: ccn down)"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("Unread", (Handler,), {"model": json.dumps(model).encode()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    unread = page.context.browser.new_page(viewport=LAPTOP)
    unread.goto(f"http://127.0.0.1:{server.server_port}/#/")
    assert "the retro table did not read" in unread.wait_for_selector(".nw-prevent .pg-fail", timeout=5000).inner_text()
    unread.close()
    server.shutdown()


def test_a_screen_that_cannot_be_drawn_shows_why_in_the_panel_instead_of_skeletons(page):
    model = json.loads(MODEL.read_text())
    model["preventions"] = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("Broken", (Handler,), {"model": json.dumps(model).encode()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    broken = page.context.browser.new_page(viewport=LAPTOP)
    broken.goto(f"http://127.0.0.1:{server.server_port}/#/")
    failure = broken.wait_for_selector("#main .pg-fail", timeout=5000)
    assert "null" in failure.inner_text()
    assert broken.locator("#main .sk").count() == 0
    broken.close()
    server.shutdown()


def test_a_refresh_asks_for_what_changed_since_its_version_and_merges_it_onto_the_rest(page):
    model = json.loads(MODEL.read_text())
    fresh = page.context.browser.new_page(viewport=LAPTOP)
    fresh.goto(page.url.split("#")[0] + "#/")
    fresh.wait_for_selector(".nw-spine")
    asked = []
    delta = {"version": model["version"] + 1, "changed": {"version": model["version"] + 1, "brief": {**model["brief"], "headline": "A fresher headline"}}}
    fresh.route(re.compile(r"/api/model\?since="), lambda route: (asked.append(route.request.url), route.fulfill(json=delta)))
    fresh.evaluate("load()")
    fresh.wait_for_selector(".nw-head:has-text('A fresher headline')")
    assert asked == [f"{page.url.split('#')[0]}api/model?since={model['version']}"]
    assert fresh.evaluate("Object.keys(state.model.prs).length") == len(model["prs"])
    fresh.close()


def spine_widths(page):
    return page.locator(".nw-spine .sp").evaluate_all("(els) => els.map((e) => e.getBoundingClientRect().width)")


def test_the_hovered_milestone_or_else_the_current_one_is_wide_with_its_details_and_the_rest_are_compressed(page):
    now(page)
    widths = spine_widths(page)
    cur = int(page.locator(".nw-slot").get_attribute("data-cur"))
    assert widths[cur] > 2 * max(w for i, w in enumerate(widths) if i != cur)
    i = next(i for i, m in enumerate(json.loads(MODEL.read_text())["milestone_state"]) if m["blockers"])
    blocked = page.locator(f'.nw-spine .sp[data-i="{i}"]')
    assert not blocked.locator(".sf").is_visible()
    rest_on(page, f'.nw-spine .sp[data-i="{i}"] .ms')
    page.wait_for_timeout(300)
    assert blocked.locator(".when").is_visible() and blocked.locator(".sf").is_visible()
    widths = spine_widths(page)
    assert widths[i] > 2 * max(w for j, w in enumerate(widths) if j != i)
    page.mouse.move(5, 5, steps=4)
    page.wait_for_timeout(900)
    assert focused(page) == cur


def test_a_live_update_keeps_the_caret_in_the_open_drawers_note_and_its_draft(page):
    page.goto(page.url.split("#")[0] + "#/review/decided~review/49001")
    note = page.locator(".pr-drawer.front [data-change] input")
    note.click()
    note.press_sequentially("hold the lock")
    note.press("ArrowLeft")
    page.evaluate("render()")
    assert page.evaluate("document.activeElement.closest('.pr-drawer.front') !== null")
    assert page.evaluate("document.activeElement.selectionStart") == len("hold the lock") - 1
    assert note.input_value() == "hold the lock"
    assert page.locator('.mr[data-peek="49001"] [data-change] input').evaluate("(el) => el.closest('[inert]') !== null")


def test_a_change_request_the_server_drops_says_not_sent_and_keeps_the_draft_for_a_retry(page):
    page.goto(page.url.split("#")[0] + "#/review/decided~review/49001")
    page.route("**/api/change", lambda route: route.abort())
    note = page.locator(".pr-drawer.front [data-change] input")
    note.fill("no lock on insert")
    note.press("Enter")
    page.wait_for_selector(".pr-drawer.front [data-change] .pill.bad")
    assert page.locator(".pr-drawer.front [data-change] button").inner_text() == "Send"
    assert page.locator(".pr-drawer.front [data-change] input").input_value() == "no lock on insert"
    page.unroute("**/api/change")


def test_after_a_tap_the_queue_moves_to_the_next_new_or_answered_row_never_one_awaiting_a_reply(page):
    """Owner 12:03 AM Nov 2: reviewing one PR kept taking him back to one he was waiting on a reply for."""
    page.goto(page.url.split("#")[0] + "#/review")
    order = page.evaluate("""() => {
      const row = (number) => ({ number, head: 'aaaa111', state: 'you', group: 'g', block: 'b' });
      state.model.board.rows = [row(1), row(2), row(3), row(4)];
      state.model.asks = { 2: [{ seq: 10, at: '2026-11-02T08:00:00Z', text: 'why port 0?', replies: [], answered: false }],
                           3: [{ seq: 11, at: '2026-11-02T08:00:00Z', text: 'what is a chat copy?', replies: [{ seq: 12, lane: 'w-1', kind: 'answer', at: '2026-11-02T08:05:00Z', text: 'A replica.' }], answered: true }] };
      const rows = state.model.board.rows;
      return [nextForYou(rows[0]).number, nextForYou(rows[2]).number, nextForYou(rows[3]).number];
    }""")
    assert order == [3, 4, 1]


def test_an_applied_order_leaves_the_review_page_and_a_reply_needing_him_shows_one_whole_line(page):
    page.goto(page.url.split("#")[0] + "#/review")
    action = "Pick where it stores on your board, and this line runs well past the old cut so that its last words prove nothing clipped it, however long a single action happens to be when he reads it. The last words"
    boxes = page.evaluate("""(action) => {
      const ask = (fields) => ({ seq: 1, at: new Date().toISOString(), text: 'store it', refs: { prs: [99] }, ...fields });
      const box = (asks) => { state.model.asks[99] = asks; return changeBox(99, 'abc1234'); };
      state.model.board.answered = { 'card-tapped': { picked: 'A' } };
      return {
        waiting: box([ask({ answered: false })]),
        applied: box([ask({ answered: true, action: null })]),
        action: box([ask({ answered: true, action, reply: 'open' })]),
        tapped: box([ask({ answered: true, action, reply: 'tapped' })]),
      };
    }""", action)
    assert "You asked; waiting on the reply" in boxes["waiting"]
    assert "fb-said" not in boxes["applied"]
    assert "The last words" in boxes["action"] and "store it" not in boxes["action"]
    assert "fb-said" not in boxes["tapped"]


def test_a_page_question_reads_received_then_past_due_in_plain_words(page):
    """Owner m311: his question sat with no acknowledgement for a minute and he had to ask in chat."""
    page.goto(page.url.split("#")[0] + "#/review")
    lines = page.evaluate("""() => {
      const ask = (due) => ({ due, holder: 'the coordinator' });
      const hour = 3600 * 1000;
      return [receivedLine(ask(new Date(Date.now() + hour).toISOString())), receivedLine(ask(new Date(Date.now() - hour).toISOString()))];
    }""")
    assert re.fullmatch(r"Received, with the coordinator, answer by .*\d [AP]M", lines[0])
    assert re.fullmatch(r"Past due: the answer was due by .*\d [AP]M", lines[1])


def test_the_first_load_asks_for_the_now_slice_and_another_tab_fetches_the_whole_model_on_demand(page):
    """Root #306148: every tab's data was parsed on the first load; Now draws from its slice and the other tabs load theirs when opened."""
    model = json.loads(MODEL.read_text())
    cut = {**model, "cut": True}
    fresh = page.context.browser.new_page(viewport=LAPTOP)
    asked = []
    fresh.route(re.compile(r"/api/model"), lambda route: (asked.append(route.request.url.split("/api/model")[1]), route.fulfill(json=cut if "tab=now" in route.request.url else model)))
    fresh.goto(page.url.split("#")[0] + "#/")
    fresh.wait_for_selector(".nw-spine")
    assert asked == ["?tab=now"] and fresh.evaluate("state.model.cut") is True
    fresh.goto(page.url.split("#")[0] + "#/work")
    fresh.wait_for_function("state.model && !state.model.cut")
    assert asked[-1] == ""
    fresh.close()


def test_the_assistant_answers_through_the_server_route_with_no_key_in_the_browser(page):
    calls = []

    def model(route):
        calls.append(route.request)
        if route.request.post_data_json.get("stream"):
            return route.fulfill(status=200, content_type="text/event-stream", body='data: {"choices": [{"delta": {"content": "From the model."}}]}\n\ndata: [DONE]\n\n')
        return route.fulfill(json={"choices": [{"message": {"content": "{}"}}]})

    fresh = page.context.browser.new_page(viewport=LAPTOP)
    requested = []
    fresh.on("request", lambda request: requested.append(request.url))
    fresh.route("**/ai/chat/completions", model)
    fresh.goto(page.url.split("#")[0] + "?ai=1#/")
    fresh.wait_for_selector(".nw-spine")
    fresh.evaluate("location.hash = '#/jump'")
    fresh.fill("#pal-q", "why is it slow?")
    fresh.press("#pal-q", "Tab")
    fresh.wait_for_function("document.getElementById('pal-ans').textContent.includes('From the model.')")
    ask = next(call for call in calls if "why is it slow?" in call.post_data)
    assert "authorization" not in ask.headers and "model" not in ask.post_data_json
    assert not any("ai.json" in url or "cerebras" in url for url in requested)
    fresh.close()


def test_a_page_served_without_the_assistant_hides_it_and_calls_no_model(page):
    fresh = page.context.browser.new_page(viewport=LAPTOP)
    requested = []
    fresh.on("request", lambda request: requested.append(request.url))
    fresh.goto(page.url.split("#")[0] + "#/")
    fresh.wait_for_selector(".nw-spine")
    fresh.evaluate("location.hash = '#/jump'")
    fresh.fill("#pal-q", "why is it slow?")
    fresh.press("#pal-q", "Tab")
    assert fresh.evaluate("document.documentElement.classList.contains('ai-on')") is False
    assert not any("/ai/" in url or "ai.json" in url for url in requested)
    fresh.close()


TAP_QUEUE = [
    {"kind": "land", "id": "land-49902", "number": 49902, "title": "Idle runners stop renewing", "frees": 3, "line": "Frees 2 changes: Team credential; Cache removal. 1 worker stopped until you tap.", "recommended": "Approve"},
    {"kind": "pick", "id": "card-tq", "card": "card-tq", "title": "Should the credential write run folders?", "frees": 1, "line": "1 worker stopped until you tap.", "recommended": "One credential", "choice": "tq-pick"},
    {"kind": "slack", "id": "slack-tq", "card": "slack-tq", "title": "Post this in a Slack thread?", "frees": 0, "line": "No worker is waiting on this post. (#49638) is in review.", "recommended": "Yes", "text": "@someone, the fix is in review.", "choice": "slack-tq-answer", "options": [{"id": "yes", "label": "Yes", "recommended": True}, {"id": "no", "label": "No", "recommended": False}], "subject": "questions-for-you--x"},
]
TAP_ROW = {"number": 49902, "head": "abc1234", "state": "you", "block": "merge-prs", "title": "Idle runners stop renewing", "key": "tq"}
TAP_CARD = {"id": "card-tq", "title": "Should the credential write run folders?", "summary": "", "key": "tqc", "choice": "tq-pick", "decides": True, "parts": [], "embed": None, "recommended": "One credential", "options": [{"id": "one", "label": "One credential", "recommended": True}, {"id": "two", "label": "Two credentials", "recommended": False}]}


def tap_screen(page):
    page.route("**/api/verdict", lambda route: route.fulfill(json={"seq": 10**9}))
    page.route("**/api/answer", lambda route: route.fulfill(json={"seq": 10**9}))
    now(page)
    page.evaluate("""([taps, row, card]) => {
      state.model.taps = taps;
      state.model.board.rows.push(row);
      state.model.board.cards.push(card);
      location.hash = '#/needs';
    }""", [TAP_QUEUE, TAP_ROW, TAP_CARD])
    page.wait_for_selector(".fm")


def tap_titles(page):
    return page.locator(".fm-lead").all_inner_texts()


def test_needs_me_shows_every_tap_in_queue_order_with_its_count_and_no_numbers(page):
    tap_screen(page)
    assert tap_titles(page) == [tap["title"] for tap in TAP_QUEUE]
    assert page.locator(".fm-icon").all_inner_texts() == ["1", "2", "3"]
    assert page.locator(".tab.on").inner_text().startswith("Needs me")
    assert "#49638" not in page.locator(".fd").inner_text()
    assert page.locator(".fm .pk.rec .pk-t").all_inner_texts() == ["One credential", "Yes"]
    now(page)


def test_an_incident_tap_leads_needs_me_even_with_nothing_waiting_on_it(page):
    tap_screen(page)
    page.evaluate("""() => {
      state.model.taps = [{ ...state.model.taps[2], id: 'slack-tq-2', urgent: true, title: 'Answer the incident thread' }, ...state.model.taps];
      render();
    }""")
    assert tap_titles(page)[0] == "Answer the incident thread"
    assert page.locator(".fd-div").all_inner_texts()[0] == "Live incidents"
    assert page.locator(".fm-icon").all_inner_texts()[:2] == ["1", "2"]
    now(page)


def chat_box(page):
    now(page)
    page.evaluate("() => { state.model.replies = []; state.model.chat_lines = []; state.model.owner_asks ??= { messages: [], taps: [], store: [] }; location.hash = '#/replies'; }")
    page.wait_for_selector(".composer textarea")
    return page.locator(".composer textarea")


def test_a_chat_box_stays_the_same_element_through_a_refresh_and_a_draft_survives_a_reload(page):
    """Owner 10:12 PM Nov 3: every refresh deleted what he was typing."""
    box = chat_box(page)
    box.click()
    page.keyboard.type("half a thought")
    assert page.evaluate("""() => {
      const el = document.querySelector('.composer textarea');
      refresh();
      render();
      return document.querySelector('.composer textarea') === el && document.activeElement === el && !state.owed;
    }""")
    page.keyboard.type(" more")
    assert box.input_value() == "half a thought more"
    page.evaluate("document.activeElement.blur(); render()")
    assert box.input_value() == "half a thought more"
    page.reload()
    assert chat_box(page).input_value() == "half a thought more"
    page.evaluate("localStorage.removeItem('draft:chat::')")
    now(page)


def test_a_refresh_waits_while_any_other_box_has_focus(page):
    now(page)
    page.evaluate("""() => {
      document.querySelector('#main').insertAdjacentHTML('beforeend', '<form id="probe"><textarea name="probe"></textarea></form>');
      document.querySelector('#probe textarea').focus();
    }""")
    assert page.evaluate("""() => {
      const el = document.querySelector('#probe textarea');
      refresh();
      return document.querySelector('#probe textarea') === el && state.owed;
    }""")
    page.evaluate("document.activeElement.blur()")
    page.wait_for_function("!state.owed")
    now(page)


def test_return_starts_a_new_line_on_a_phone_and_no_box_is_small_enough_for_ios_to_zoom(page):
    context = page.context.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    phone = context.new_page()
    phone.goto(page.url.split("#")[0] + "#/replies")
    box = phone.locator(".composer textarea")
    box.tap()
    phone.keyboard.type("first line")
    phone.keyboard.press("Enter")
    phone.keyboard.type("second line")
    assert box.input_value() == "first line\nsecond line"
    assert not phone.locator(".compose-bottom > span").is_visible()
    assert phone.locator(".compose-bottom button").is_visible()
    assert phone.evaluate("Math.min(...[...document.querySelectorAll('input, textarea')].map((el) => parseFloat(getComputedStyle(el).fontSize)))") >= 16
    context.close()


def test_a_failed_chat_line_returns_to_its_box_and_a_sent_one_clears_it(page):
    box = chat_box(page)
    box.click()
    page.keyboard.type("send me")
    page.route("**/api/reply", lambda route: route.fulfill(status=502, json={"error": "the records were down"}))
    page.evaluate("document.querySelector('.composer').requestSubmit()")
    page.wait_for_function("rp.failed.get('')")
    assert box.input_value() == "send me"
    assert page.evaluate("localStorage.getItem('draft:chat::')") == "send me"
    page.unroute("**/api/reply")
    page.route("**/api/reply", lambda route: route.fulfill(json={"seq": 5}))
    page.evaluate("document.querySelector('.composer').requestSubmit()")
    page.wait_for_function("rp.sent.at(-1)?.seq === 5")
    assert box.input_value() == ""
    assert page.evaluate("localStorage.getItem('draft:chat::')") is None
    page.unroute("**/api/reply")
    page.evaluate("rp.sent.length = 0; rp.failed.clear()")
    now(page)


WHOLE_CARD = {"id": "card-whole", "title": "Should it write run folders?", "summary": "One credential covers it.", "key": "whole", "choice": "whole-pick", "decides": True, "embed": None, "recommended": "Yes", "options": [{"id": "yes", "label": "Yes", "recommended": True}, {"id": "no", "label": "No", "recommended": False}], "thread": [{"who": "you", "text": "what does it risk?", "at": 1}, {"who": "answer", "text": "A client bug could overwrite a run.", "at": 2}], "parts": [
    {"kind": "record", "id": "r", "title": "Background", "facts": [{"label": "Your words", "value": "one per team"}]},
    {"kind": "page", "id": "m", "md": "**Runs** live in the bucket."},
    {"kind": "table", "id": "t", "columns": [{"key": "a", "label": "Option"}], "rows": [{"a": "One credential"}]},
    {"kind": "image", "id": "i", "src": "/asset/abc", "alt": "The page today"},
    {"kind": "choice", "id": "whole-pick", "prompt": "Pick one", "answer": None, "options": [{"id": "yes", "label": "Yes", "hint": "One mint a team", "facts": [], "recommended": True}]},
]}


def whole_card(page):
    now(page)
    page.evaluate("""(card) => {
      state.model.board.cards.push(card);
      state.model.needs.push({ kind: 'card', card, why: '' });
      location.hash = '#/~question/card-whole';
    }""", WHOLE_CARD)
    page.wait_for_selector("#card-card-whole")


def drop_whole_card(page):
    now(page)
    page.evaluate("""() => {
      state.model.needs = state.model.needs.filter((item) => item.card?.id !== 'card-whole');
      state.model.board.cards = state.model.board.cards.filter((card) => card.id !== 'card-whole');
    }""")


def test_a_card_renders_whole_on_the_dashboard_with_no_link_to_the_board(page):
    whole_card(page)
    card = page.locator("#card-card-whole")
    card.locator(".pk-more > summary").click()
    words = card.inner_text().lower()
    for said in ("One credential covers it.", "Background", "one per team", "Runs live in the bucket.", "One credential", "One mint a team", "what does it risk?", "A client bug could overwrite a run."):
        assert said.lower() in words
    assert card.locator("img").get_attribute("src") == "/asset/abc"
    assert card.locator('a[href*="block="]').count() == 0
    assert "on your board" not in words
    drop_whole_card(page)


def test_a_question_on_a_card_goes_down_the_boards_note_path_and_clears_once_sent(page):
    page.route("**/api/answer", lambda route: route.fulfill(json={"seq": 10**9}))
    whole_card(page)
    box = page.locator("#card-card-whole .bq-ask textarea")
    box.fill("is it reversible?")
    with page.expect_request("**/api/answer") as asked:
        box.press("Enter")
    assert asked.value.post_data_json == {"kind": "note", "block": "card-whole", "text": "is it reversible?"}
    page.wait_for_function("document.querySelector('#card-card-whole .bq-ask textarea').value === ''")
    assert page.evaluate("localStorage.getItem('draft:card-note:card-whole:')") is None
    assert "The answer lands on this card" in page.locator("#card-card-whole").inner_text()
    page.unroute("**/api/answer")
    drop_whole_card(page)


def test_a_click_anywhere_on_a_tap_row_opens_its_window_and_a_button_stays_a_button(page):
    tap_screen(page)
    row = page.locator('.fm[data-tap="card-tq"]')
    row.locator(".fm-lead").click()
    page.wait_for_selector(".pr-drawer.front")
    assert "question/card-tq" in page.evaluate("location.hash")
    page.evaluate("location.hash = '#/needs'")
    page.wait_for_selector(".fm")
    page.evaluate("""([tap, card]) => {
      state.model.taps = [tap];
      state.model.board.cards.push(card);
      render();
    }""", [{**TAP_QUEUE[1], "id": "card-btn", "card": "card-btn", "choice": "btn-pick"}, {**TAP_CARD, "id": "card-btn", "choice": "btn-pick"}])
    with page.expect_request("**/api/answer"):
        page.locator('.fm[data-tap="card-btn"] .pk').first.click()
    assert "question" not in page.evaluate("location.hash")
    page.evaluate("ov.sent.clear(); ov.kept.clear(); state.model.board.cards = state.model.board.cards.filter((card) => card.id !== 'card-btn')")
    page.unroute("**/api/verdict")
    page.unroute("**/api/answer")
    now(page)


def test_review_lists_pull_requests_only_and_its_count_leaves_out_questions(page):
    now(page)
    page.evaluate("""() => {
      window.keptNeeds = state.model.needs;
      state.model.needs = [{ kind: 'card', card: { id: 'card-q', title: 'A question', decides: true, parts: [], options: [] }, why: '' }];
      state.model.board.cards.push({ id: 'card-note', title: 'A note', summary: 'For reading', decides: false, parts: [], options: [] });
      location.hash = '#/review';
    }""")
    page.wait_for_selector(".pg .pg-lead")
    words = page.locator("#main").inner_text()
    assert "QUESTIONS" not in words.upper()
    assert "A note" not in words
    assert not re.search(r"\d", page.locator("#tabs a", has_text="Review").inner_text())
    page.evaluate("location.hash = '#/needs'")
    page.wait_for_selector(".mr.bq.note")
    assert "A note" in page.locator("#main").inner_text()
    page.evaluate("state.model.needs = window.keptNeeds; state.model.board.cards = state.model.board.cards.filter((card) => card.id !== 'card-note')")
def test_chat_shows_each_ask_as_one_title_and_state_with_its_answer_under_it(page):
    now(page)
    page.evaluate("""() => {
      state.model.board.answered = {};
      state.model.chat_lines = [];
      state.model.coordinator_overdue = [];
      state.model.replies = [
        { reply: { id: 'ans', title: 'Done, nothing for you: Fixed', md: 'Done, nothing for you. The box holds now', type: 'done', status: 'done', order: 'm1.2' }, at: '2026-11-03T14:00:00Z', surfaces: [] },
        { reply: { id: 'later', title: 'Something else', md: 'Another thing', type: 'explanation', status: 'info' }, at: '2026-11-03T13:00:00Z', surfaces: [] },
      ];
      state.model.owner_asks = { messages: [
        { m: '1.1', iso: '2026-11-03T12:00:00Z', title: 'Fix the typing box', text: 'Please fix the typing box. It resets on every refresh and I lose what I typed.', state: 'asked', ack: 'routed', lanes: [], prs: [], answer: '' },
        { m: '1.2', iso: '2026-11-03T12:00:00Z', text: 'and the second part of that same message', state: 'asked', ack: 'routed', lanes: [], prs: [], answer: '' },
        { m: 2, iso: '2026-11-03T12:30:00Z', text: 'A second ask that nobody answered with words', state: 'asked', ack: 'routed', lanes: ['a-1'], prs: [], answer: '' },
      ], taps: [], store: [] };
      location.hash = '#/chat';
    }""")
    page.wait_for_selector(".cw .thread")
    assert page.locator(".cw .ct-bubble").count() == 0
    asks = page.locator(".cw .thread:has(.ask-head)")
    assert asks.count() == 2
    second = asks.nth(0)
    assert "A second ask" in second.locator(".ask-title").inner_text()
    assert second.locator(".pill").inner_text() == "In progress"
    first = asks.nth(1)
    assert first.locator(".ask-title").inner_text() == "Fix the typing box"
    assert first.locator(".pill").inner_text() == "Completed"
    assert not first.locator(".ask-detail").is_visible()
    first.locator(".ask-head").click()
    whole = first.locator(".ask-detail").inner_text()
    assert "It resets on every refresh" in whole and "the second part" in whole
    assert first.locator(".decision-card h3").inner_text() == "Fixed"
    assert "nothing for you" not in page.locator(".cw").inner_text().lower()
    assert "Another thing" in page.locator(".thread.loose").inner_text()
    page.evaluate("cw.open.clear()")
    now(page)


def test_a_pick_in_flight_or_failed_holds_the_page_from_reloading_and_a_recorded_one_does_not(page):
    now(page)
    assert page.evaluate("""() => {
      const held = (sent) => { ov.sent.set('probe', sent); const out = pickUnsettled(); ov.sent.delete('probe'); return out; };
      return [held({}), held({ error: 'x' }), held({ recorded: true })];
    }""") == [True, True, False]


def test_needs_me_fits_a_phone(page):
    tap_screen(page)
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.set_viewport_size(LAPTOP)
    now(page)


def test_a_tap_on_needs_me_goes_down_the_boards_path_and_shows_what_he_picked_in_its_row(page):
    tap_screen(page)
    with page.expect_request("**/api/answer") as asked:
        page.locator('[data-tap="slack-tq"] .pk.rec').click()
    assert asked.value.post_data_json == {"kind": "choice", "block": "slack-tq-answer", "options": ["yes"], "subject": "questions-for-you--x"}
    page.wait_for_selector('[data-tap="slack-tq"] .pk-picked')
    assert "Yes" in page.locator('[data-tap="slack-tq"] .pk-picked').inner_text()
    assert page.locator('[data-tap="slack-tq"] .pk').count() == 0
    with page.expect_request("**/api/verdict") as decided:
        page.locator('[data-tap="land-49902"] .btn.pri').click()
    assert decided.value.post_data_json == {"block": "merge-prs", "number": 49902, "verdict": "approved", "head": "abc1234"}
    page.wait_for_function("!document.querySelector('[data-tap=\"land-49902\"]')")
    with page.expect_request("**/api/answer") as picked:
        page.locator('[data-tap="card-tq"] .pk.rec').click()
    assert picked.value.post_data_json == {"kind": "choice", "block": "tq-pick", "options": ["one"]}
    page.wait_for_selector('[data-tap="card-tq"] .pk-picked')
    assert page.locator(".fd-h h1").inner_text() == "Nothing waits on your tap."
    page.evaluate("ov.sent.clear(); ov.kept.clear()")
    page.unroute("**/api/verdict")
    page.unroute("**/api/answer")
    now(page)


FOLDED_PICK = {"kind": "choice", "id": "fold-pick", "prompt": "Which one?", "answer": None, "options": [
    {"id": "one", "label": "One credential", "hint": "", "note": "as you said", "facts": [{"label": "For", "value": "One mint a team.", "tone": "good"}, {"label": "Against", "value": "A client bug could overwrite a run.", "tone": "bad"}], "recommended": True},
    {"id": "two", "label": "Two credentials", "hint": "", "facts": [{"label": "For", "value": "Runs stay safe.", "tone": "good"}, {"label": "Against", "value": "Two mints a team.", "tone": "bad"}], "recommended": False},
]}
FOLDED_CARD = {"id": "card-fold", "title": "Should it write run folders?", "summary": "One permission per credential.", "key": "fold", "choice": "fold-pick", "decides": True, "embed": None, "recommended": "One credential", "options": [{"id": "one", "label": "One credential", "recommended": True}, {"id": "two", "label": "Two credentials", "recommended": False}], "parts": [{"kind": "page", "id": "fold-md", "md": "The run reader waits on this."}, FOLDED_PICK]}
FOLDED_TAP = {"kind": "pick", "id": "card-fold", "card": "card-fold", "title": FOLDED_CARD["title"], "frees": 2, "line": "Frees 2 changes.", "recommended": "One credential", "choice": "fold-pick"}


def folded_screen(page, answer=None):
    page.route("**/api/answer", lambda route: route.fulfill(json=answer or {"seq": 10**9}))
    now(page)
    page.evaluate("""([tap, card]) => {
      state.model.taps = [tap];
      state.model.board.cards.push(card);
      state.model.needs.push({ kind: 'card', card, why: '' });
      location.hash = '#/needs';
    }""", [FOLDED_TAP, FOLDED_CARD])
    page.wait_for_selector('.fm[data-tap="card-fold"]')
    return page.locator('.fm[data-tap="card-fold"]')


def drop_folded(page):
    page.evaluate("""() => {
      ov.sent.clear();
      ov.kept.clear();
      state.open.delete('explain-card-fold');
      state.taps.length = 0;
      state.model.taps = [];
      state.model.board.cards = state.model.board.cards.filter((card) => card.id !== 'card-fold');
      state.model.needs = state.model.needs.filter((item) => item.card?.id !== 'card-fold');
    }""")
    page.unroute("**/api/answer")
    now(page)


def test_a_pick_card_shows_each_option_once_and_the_block_is_the_tap_target(page):
    row = folded_screen(page)
    assert row.inner_text().count("One credential") == 1
    assert row.locator("button[data-choose]").count() == 0
    best = row.locator('.pk[data-choose="one"]')
    assert "Recommended" in best.inner_text() and "as you said" in best.inner_text()
    assert best.locator(".pk-facts .good").inner_text().endswith("One mint a team.")
    assert best.locator(".pk-facts .bad").inner_text().endswith("A client bug could overwrite a run.")
    assert not row.locator(".pk-more .md").is_visible()
    row.locator(".pk-more > summary").click()
    assert row.locator(".pk-more .md").inner_text() == "The run reader waits on this."
    assert row.locator(".pk-more .bq-ask textarea").is_visible()
    with page.expect_request("**/api/answer") as picked:
        row.locator('.pk[data-choose="two"] .pk-facts .bad').click()
    assert picked.value.post_data_json == {"kind": "choice", "block": "fold-pick", "options": ["two"]}
    page.wait_for_selector('.fm.done[data-tap="card-fold"] .pk-picked')
    assert "Two credentials" in row.locator(".pk-picked").inner_text()
    assert page.locator(".fd-h h1").inner_text() == "Nothing waits on your tap."
    drop_folded(page)


def test_a_file_and_line_on_a_card_hovers_the_line_from_github(page):
    page.route("**/api/source*", lambda route: route.fulfill(json={"lines": [{"n": 55, "text": "const before = 1"}, {"n": 56, "text": "const cited = 2"}]}))
    row = folded_screen(page)
    page.evaluate("""() => {
      state.model.board.cards.find((card) => card.id === 'card-fold').parts[0].md = 'One permission (`api/src/modules/r2.ts:56`).';
      render();
    }""")
    row.locator(".pk-more > summary").click()
    cite = row.locator(".ln-src")
    assert cite.inner_text() == "r2.ts:56"
    assert cite.get_attribute("href").endswith("/blob/HEAD/api/src/modules/r2.ts#L56")
    with page.expect_request("**/api/source*") as asked:
        cite.hover()
    assert "path=api%2Fsrc%2Fmodules%2Fr2.ts" in asked.value.url and "line=56" in asked.value.url
    page.wait_for_selector("#srctip .hl")
    assert page.locator("#srctip .hl").inner_text().endswith("const cited = 2")
    page.mouse.move(0, 0)
    page.wait_for_function("!document.querySelector('#srctip').matches(':popover-open')")
    page.unroute("**/api/source*")
    drop_folded(page)


def test_a_release_id_becomes_its_name_only_beside_that_name(page):
    now(page)
    assert page.evaluate("""() => {
      state.model.now.releases.push({ id: 'R9', name: 'Desktop app', parts: [], state: '' });
      const said = [words('R9 Desktop app ships'), words('On R9, one credential carries one permission')];
      state.model.now.releases.pop();
      return said;
    }""") == ["Desktop app ships", "On R9, one credential carries one permission"]


def test_an_answer_the_server_refuses_leaves_the_blocks_tappable_with_the_reason(page):
    row = folded_screen(page, {"error": "the board is down"})
    row.locator('.pk[data-choose="one"]').click()
    page.wait_for_selector('.fm[data-tap="card-fold"] .nsent.bad')
    assert "the board is down" in row.locator(".nsent.bad").inner_text()
    assert row.locator(".pk[data-choose]").count() == 2 and row.locator(".pk.off, .pk.on").count() == 0
    drop_folded(page)


def test_a_written_answer_goes_down_the_boards_other_path_and_shows_in_place(page):
    row = folded_screen(page)
    box = row.locator(".pk-other textarea")
    box.fill("1 but only for chats")
    with page.expect_request("**/api/answer") as written:
        box.press("Enter")
    assert written.value.post_data_json == {"kind": "choice", "block": "fold-pick", "options": [], "other": "1 but only for chats"}
    page.wait_for_selector('.fm.done[data-tap="card-fold"] .pk-picked')
    assert "1 but only for chats" in row.locator(".pk-picked").inner_text()
    assert page.evaluate("localStorage.getItem('draft:pick-other:fold-pick:')") is None
    drop_folded(page)


def test_a_number_key_picks_only_in_the_one_card_he_opened(page):
    asked = []
    folded_screen(page)
    page.on("request", lambda request: asked.append(request) if request.url.endswith("/api/answer") else None)
    page.keyboard.press("1")
    assert asked == []
    page.evaluate("location.hash = '#/needs~question/card-fold'")
    page.wait_for_selector(".pr-drawer.front .pk-set")
    with page.expect_request("**/api/answer") as picked:
        page.keyboard.press("2")
    assert picked.value.post_data_json == {"kind": "choice", "block": "fold-pick", "options": ["two"]}
    page.wait_for_selector(".pr-drawer.front .pk-picked")
    assert "Two credentials" in page.locator(".pr-drawer.front .pk-picked").inner_text()
    drop_folded(page)


CHAT_MODEL = """(n) => {
  const reply = (id, extra) => ({ reply: { id, title: `Title ${id}`, md: `Body ${id}`, ...extra }, at: '2026-11-03T20:00:00Z', surfaces: [] });
  const card = (id, labels) => ({ id: `card-${id}`, title: id, summary: 'Why', chips: [], prs: [], parts: [], key: id, decides: true, choice: `${id}-choice`, options: labels.map((label, i) => ({ id: `${id}-${i + 1}`, label, recommended: i === 0 })) });
  state.model.replies = [
    reply('open4', { type: 'for_you', status: 'needs_you', picks: ['p', 'q', 'r'] }),
    reply('open3', { type: 'for_you', status: 'needs_you', picks: ['a', 'b', 'c'] }),
    reply('open2', { type: 'for_you', status: 'needs_you', picks: ['Yes', 'No'] }),
    { ...reply('plain', { type: 'for_you', status: 'needs_you' }), at: new Date().toISOString() },
    reply('stale', { type: 'for_you', status: 'needs_you' }),
    reply('old', { type: 'for_you', status: 'needs_you', picks: ['x', 'y'] }),
    reply('routed', { type: 'update', status: 'progress', order: 'm7' }),
    reply('change', { type: 'explanation', status: 'info', md: 'See #77001 for it', links: [{ url: 'https://example.ts.net:8665/one-pagers/how-it-works/', label: 'How it works' }, { url: 'https://example.slack.com/x', label: 'The thread' }] }),
    ...Array.from({ length: n }, (_, i) => ({ ...reply(`r${i}`, { type: 'update', status: 'info' }), at: '2026-11-03T19:00:00Z' })),
  ];
  state.model.board.cards = [card('open2', ['Yes', 'No']), card('open3', ['a', 'b', 'c']), card('open4', ['p', 'q', 'r'])];
  state.model.board.answered = {};
  state.model.prs[77001] = { title: 'dashboard: 🐛 fix the box', test_state: 'passed', ai_review: 'success', changed_files: '3', head: 'abc1234' };
  state.model.board.rows = state.model.board.rows.filter((row) => row.number !== 77001).concat({ number: 77001, head: 'abc1234', title: 'fix the box', change: 'Keeps what he typed', state: 'you', blocker: '', plan: '', risk: '', review: '' });
  state.drive.set(77001, { number: 77001, state: 'you' });
  state.model.owner_asks = { messages: [{ m: 7, iso: '2026-11-03T21:00:00-08:00', text: 'my line', state: 'asked', ack: 'routed', lanes: ['a-1'], prs: [] }], taps: [], store: [] };
  state.model.chat_lines = [];
  render();
}"""


def open_chat(page, n=0):
    now(page)
    page.evaluate(CHAT_MODEL, n)
    page.evaluate("location.hash = '#/replies'")
    page.wait_for_selector(".cw .thread")


def test_chat_leads_with_what_needs_him_and_then_the_newest(page):
    open_chat(page, 100)
    keys = page.evaluate("[...document.querySelectorAll('.cw .thread')].map((el) => el.dataset.k)")
    assert set(keys[:4]) == {"reply:open2", "reply:open3", "reply:open4", "reply:plain"}
    assert keys[4] == "line:m7"
    assert page.locator(".dayline .eyebrow").first.inner_text().lower() == "needs you first"
    now(page)


def test_a_reply_never_shows_the_comments_the_communicator_left_for_its_tools(page):
    now(page)
    assert page.evaluate("plain('It ran. <!-- Slack C0C5SEUJK5L/1793776460.701879; cci --> Done.')") == "It ran. Done."


def test_a_status_table_nobody_has_updated_says_so_and_a_fresh_one_does_not(page):
    now(page)
    assert page.evaluate("""() => {
      const clockAgo = (hours) => new Date(Date.now() - hours * 3600000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
      const today = new Date().getHours();
      return [today < 3 || tableStale(clockAgo(3)), tableStale(clockAgo(0))];
    }""") == [True, False]


def test_a_thread_reply_names_its_parent_and_shows_under_it(page):
    open_chat(page)
    line = page.locator(".thread", has_text="my line")
    assert line.locator(".ask-head .pill").inner_text() == "In progress"
    page.route("**/api/reply", lambda route: route.fulfill(json={"seq": 42}))
    line.locator(".thread-foot [data-chat-thread]").click()
    assert page.evaluate("document.activeElement.name") == "chat"
    line.locator(".reply-editor textarea").fill("on that")
    with page.expect_request("**/api/reply") as sent:
        line.locator(".reply-editor textarea").press("Enter")
    body = sent.value.post_data_json
    assert body["on"] == "line:m7" and body["text"] == "on that" and "m7" in body["about"]
    page.wait_for_function("rp.sent.at(-1)?.seq === 42")
    page.evaluate("state.model.chat_lines = [{ seq: 42, at: '2026-11-04T06:00:00Z', on: 'line:m7', text: 'on that' }]; render()")
    assert "on that" in line.locator(".reply-receipt").inner_text()
    assert page.locator(".ask-title", has_text="on that").count() == 0
    page.unroute("**/api/reply")
    page.evaluate("rp.sent.length = 0")
    now(page)


def test_any_object_threads_and_the_communicator_gets_what_it_hangs_on(page):
    open_chat(page)
    card = page.locator("#rp-change")
    page.route("**/api/reply", lambda route: route.fulfill(json={"seq": 43}))
    card.hover()
    card.locator(".obj-reply").click()
    card.locator(".reply-editor textarea").fill("about this")
    with page.expect_request("**/api/reply") as sent:
        card.locator(".reply-editor textarea").press("Enter")
    assert sent.value.post_data_json["on"] == "reply:change"
    page.unroute("**/api/reply")
    page.evaluate("rp.sent.length = 0")
    now(page)


def test_a_yes_no_answers_on_the_row_and_a_bigger_question_opens_on_hover(page):
    open_chat(page)
    assert page.locator("#rp-open2 .inline-picks .action").all_inner_texts() == ["Yes", "No"]
    assert page.locator("#rp-old [data-choose]").count() == 0
    assert page.locator("#rp-old .ct-closed").count() == 1
    assert page.locator("#rp-plain [data-chat-done]").count() == 1
    assert page.locator("#rp-stale [data-chat-done]").count() == 0
    options = page.locator("#rp-open3 .choices")
    assert not options.is_visible()
    page.locator("#rp-open3").hover()
    assert options.is_visible()
    assert options.locator("[data-choose]").count() == 3
    page.mouse.move(0, 0)
    assert not options.is_visible()
    page.locator("#rp-open3 [data-q-pin]").click()
    page.mouse.move(0, 0)
    assert options.is_visible()
    page.keyboard.press("Escape")
    now(page)


def test_a_pick_tapped_in_chat_posts_its_answer_inline_on_hover_and_in_the_window(page):
    page.route("**/api/answer", lambda route: route.fulfill(json={"seq": 10**9}))
    open_chat(page)
    with page.expect_request("**/api/answer") as inline:
        page.locator('#rp-open2 [data-choose="open2-2"]').click()
    assert inline.value.post_data_json == {"kind": "choice", "block": "open2-choice", "options": ["open2-2"]}
    page.wait_for_selector("#rp-open2 .obj-picked")
    assert "You picked" in page.locator("#rp-open2 .obj-picked").inner_text()
    page.locator("#rp-open3").hover()
    page.locator("#rp-open3 .write-in textarea").fill("neither")
    with page.expect_request("**/api/answer") as written:
        page.locator("#rp-open3 .write-in button").click()
    assert written.value.post_data_json == {"kind": "choice", "block": "open3-choice", "options": [], "other": "neither"}
    page.wait_for_selector("#rp-open3 .obj-picked")
    page.evaluate("rp.modal = 'card-open4'; render()")
    with page.expect_request("**/api/answer") as windowed:
        page.locator('#chatmodal [data-choose="open4-1"]').click()
    assert windowed.value.post_data_json == {"kind": "choice", "block": "open4-choice", "options": ["open4-1"]}
    page.evaluate("ov.sent.clear(); ov.kept.clear(); state.taps.length = 0; rp.modal = null; render()")
    page.unroute("**/api/answer")
    now(page)


def test_a_change_named_in_a_reply_is_a_small_card_with_real_approve_and_hold(page):
    open_chat(page)
    card = page.locator("#pr-77001")
    assert card.locator("h3").inner_text() == "Fix the box"
    assert "Keeps what he typed" in card.inner_text()
    assert page.locator(".pr-card#pr-77001").count() == 1
    page.route("**/api/verdict", lambda route: route.fulfill(json={}))
    with page.expect_request("**/api/verdict") as held:
        card.locator("[data-hold-row]").click()
    assert held.value.post_data_json["verdict"] == "rejected" and held.value.post_data_json["number"] == 77001
    page.evaluate("pending.number = null; pending.error = null; boardRow(77001).state = 'you'; patchChat()")
    with page.expect_request("**/api/verdict") as approved:
        card.locator("[data-approve]").click()
    assert approved.value.post_data_json["verdict"] == "approved" and approved.value.post_data_json["head"] == "abc1234"
    page.unroute("**/api/verdict")
    page.evaluate("pending.number = null; pending.error = null")
    now(page)


def test_a_one_pager_previews_small_reads_full_on_hover_and_opens_in_the_window(page):
    page.route("https://example.ts.net:8665/**", lambda route: route.fulfill(body="<h1>Doc</h1>", content_type="text/html"))
    open_chat(page)
    tile = page.locator(".document-card .diagram-trigger")
    popover = tile.locator(".diagram-popover")
    assert not popover.is_visible()
    assert tile.locator(".diagram-popover iframe").get_attribute("src") is None
    tile.hover()
    assert popover.is_visible()
    assert "how-it-works" in tile.locator(".diagram-popover iframe").get_attribute("src")
    page.mouse.move(0, 0)
    page.locator(".document-card .doc-footer button").click()
    assert "how-it-works" in page.locator("#chatmodal iframe").get_attribute("src")
    page.locator("#chatmodal [data-chat-close]").click()
    assert not page.evaluate("document.querySelector('#chatmodal').open")
    assert page.locator(".decision-tail a", has_text="The thread").count() == 1
    page.unroute("https://example.ts.net:8665/**")
    now(page)


def test_a_refresh_leaves_a_thread_it_did_not_change_as_the_same_elements(page):
    open_chat(page)
    assert page.evaluate("""() => {
      const frame = document.querySelector('.document-card iframe');
      const thread = document.querySelector('[data-k="line:m7"]');
      thread.querySelector('.ask-head').click();
      state.model.replies = [{ reply: { id: 'fresh', title: 'Fresh', md: 'New', type: 'explanation', status: 'info' }, at: new Date().toISOString(), surfaces: [] }, ...state.model.replies];
      render();
      const kept = document.querySelector('.document-card iframe') === frame && document.querySelector('[data-k="line:m7"]') === thread && thread.classList.contains('open');
      cw.open.clear();
      return kept && Boolean(document.querySelector('[data-k="reply:fresh"]'));
    }""")
    now(page)


def test_a_reply_box_with_text_survives_a_change_to_its_own_thread(page):
    open_chat(page)
    line = page.locator('[data-k="line:m7"]')
    line.locator(".thread-foot [data-chat-thread]").click()
    line.locator(".reply-editor textarea").fill("half a reply")
    assert page.evaluate("""() => {
      const box = document.querySelector('[data-k="line:m7"] .reply-editor textarea');
      box.setSelectionRange(2, 4);
      state.model.replies = [{ reply: { id: 'arrived', title: 'Arrived', md: 'New answer', type: 'done', status: 'done', order: 'm7' }, at: new Date().toISOString(), surfaces: [] }, ...state.model.replies];
      render();
      return document.querySelector('[data-k="line:m7"] .reply-editor textarea') === box && document.activeElement === box && box.selectionStart === 2 && box.selectionEnd === 4;
    }""")
    assert line.locator(".reply-editor textarea").input_value() == "half a reply"
    assert line.locator(".ask-head .pill").inner_text() == "Completed"
    assert line.locator("#rp-arrived").count() == 1
    page.evaluate("localStorage.removeItem('draft:chat:line:m7:'); cw.replying.clear(); document.querySelector('[data-k=\"line:m7\"] form').remove()")
    now(page)


def test_a_pick_keeps_its_receipt_after_the_board_drops_the_card(page):
    open_chat(page)
    page.evaluate("state.model.board.cards = state.model.board.cards.filter((c) => c.id !== 'card-open2'); state.model.board.answered['card-open2'] = { picked: 'No' }; render()")
    assert "You picked No" in page.locator("#rp-open2 .obj-picked").inner_text()
    assert page.locator("#rp-open2 .ct-closed").count() == 0
    now(page)


def test_an_ask_typed_here_keeps_its_thread_replies_and_draft_when_its_ledger_row_arrives(page):
    open_chat(page)
    page.evaluate("""() => {
      state.model.chat_lines = [{ seq: 42, at: new Date().toISOString(), on: '', text: 'typed just now' }];
      state.model.replies = [{ reply: { id: 'typed-answer', title: 'Typed answer', md: 'Here', type: 'explanation', status: 'info', on: 'seq:42' }, at: new Date().toISOString(), surfaces: [] }, ...state.model.replies];
      render();
    }""")
    thread = page.locator('[data-k="seq:42"]')
    assert thread.locator("#rp-typed-answer").count() == 1
    thread.locator(".thread-foot [data-chat-thread]").click()
    thread.locator(".reply-editor textarea").fill("a draft")
    assert page.evaluate("""() => {
      const before = document.querySelector('[data-k="seq:42"]');
      state.model.owner_asks.messages.push({ m: 8, iso: new Date().toISOString(), title: 'Typed ask', text: 'typed just now', state: 'answered', ack: 'done', lanes: [], prs: [] });
      render();
      return document.querySelector('[data-k="seq:42"]') === before;
    }""")
    assert thread.locator(".ask-title").inner_text() == "Typed ask"
    assert thread.locator(".reply-editor textarea").input_value() == "a draft"
    assert page.locator('[data-k="line:m8"]').count() == 0
    page.evaluate("localStorage.removeItem('draft:chat:seq:42:'); cw.replying.clear(); document.querySelector('[data-k=\"seq:42\"] form').remove(); state.model.chat_lines = []; render()")
    now(page)


def test_a_notification_shows_its_reply_past_any_filter_or_attention_cap(page):
    open_chat(page)
    page.evaluate("""() => {
      const late = Array.from({ length: 8 }, (_, i) => ({ reply: { id: `ask${i}`, title: `Ask ${i}`, md: 'x', type: 'for_you', status: 'needs_you' }, at: new Date(Date.now() - i * 60000).toISOString(), surfaces: [] }));
      state.model.replies = [...late, ...state.model.replies];
      cw.filter = 'completed';
      cw.query = 'nothing matches this';
      render();
      chatAim('ask7');
      render();
    }""")
    assert page.locator("#rp-ask7").is_visible()
    assert page.evaluate("cw.focus") is None
    assert page.evaluate("cw.filter") == "all"
    page.evaluate("render()")
    assert page.locator("#rp-ask7").is_visible()
    page.evaluate("cw.limit = 30; cw.lead = 5")
    now(page)


def test_arriving_from_another_page_lands_on_the_notified_reply_not_the_old_scroll(page):
    open_chat(page, 100)
    page.evaluate("location.hash = '#/plan'")
    page.wait_for_function("pageRoute().screen === 'plan'")
    page.evaluate("chatAim('r60'); location.hash = '#/replies'")
    page.wait_for_selector("#rp-r60")
    page.wait_for_function("cw.focus === null")
    box = page.evaluate("document.querySelector('#rp-r60').getBoundingClientRect().top")
    assert 0 <= box <= page.viewport_size["height"]
    page.evaluate("cw.limit = 30")
    now(page)


def test_the_sidebar_holds_the_navigation_on_a_wide_screen_and_a_bar_on_a_phone(page):
    now(page)
    assert page.evaluate("getComputedStyle(document.querySelector('.top')).position") == "fixed"
    assert page.locator("#tabs .tab .ico").count() == 8
    assert page.evaluate("document.querySelector('.tab').getBoundingClientRect().left") < 194 < page.evaluate("document.querySelector('#main').getBoundingClientRect().left + 1")
    size = page.viewport_size
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("getComputedStyle(document.querySelector('.top')).position") == "sticky"
    assert not page.locator("#tabs .tab .ico").first.is_visible()
    page.set_viewport_size(size)
    now(page)


def test_chat_counts_what_waits_on_him_once_and_never_calls_it_needs_me_twice(page):
    asked = dt.datetime.now(dt.timezone.utc).isoformat()
    question = {"reply": {"id": "q", "title": "Pick one", "md": "Which?", "type": "for_you"}, "at": asked, "surfaces": []}
    chat_with(page, [question], [ask(3, "Delivered")])
    assert page.locator('[data-cw-filter="needs"]').inner_text().startswith("Questions")
    assert int(page.locator('[data-cw-filter="needs"] small').inner_text()) == int(page.locator(".tab.on .count").inner_text()) >= 1


def test_the_phone_tab_bar_brings_the_current_screen_into_view_and_keeps_a_swipe_through_a_refresh(page):
    now(page)
    size = page.viewport_size
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("location.hash = '#/history'")
    page.wait_for_selector("#tabs .tab.on[href='#/history']")
    box = page.locator("#tabs .tab.on").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    page.evaluate("document.querySelector('#tabs').scrollLeft = 0; refresh()")
    assert page.evaluate("document.querySelector('#tabs').scrollLeft") == 0
    page.set_viewport_size(size)
    now(page)


@pytest.mark.parametrize("n", [1, 1000])
def test_the_chat_draws_a_bounded_window_at_any_length(page, n):
    open_chat(page, n)
    assert page.locator(".cw .thread").count() <= 30
    now(page)


def chat_with(page, replies, messages):
    now(page)
    page.evaluate("""([replies, messages]) => {
      state.model.board.answered = {};
      state.model.chat_lines = [];
      state.model.coordinator_overdue = [];
      state.model.replies = replies;
      state.model.owner_asks = { messages, taps: [], store: [] };
      cw.filter = 'all';
      cw.query = '';
      location.hash = '#/chat';
      render();
    }""", [replies, messages])
    page.wait_for_selector(".cw .thread")


def ask(m, title, **rest):
    return {"m": m, "iso": "2026-11-03T12:00:00Z", "title": title, "text": f"{title}, please", "state": "answered", "ack": "done", "lanes": [], "prs": [], "answer": "", **rest}


def test_a_phone_gets_room_to_tap_reply_and_sees_when_each_ask_came_in(page):
    context = page.context.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    phone = context.new_page()
    phone.goto(page.url.split("#")[0] + "#/")
    news = {"reply": {"id": "news", "title": "An update", "md": "What moved", "type": "update", "status": "progress"}, "at": "2026-11-03T13:00:00Z", "surfaces": []}
    chat_with(phone, [news], [ask(3, "Delivered")])
    assert phone.locator(".ask-meta time").is_visible()
    reply = phone.locator(".obj-reply").first
    reply.scroll_into_view_if_needed()
    box = reply.bounding_box()
    assert box["height"] < 24
    assert phone.evaluate("([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('.obj-reply'))", [box["x"] + box["width"] / 2, box["y"] - 8])
    context.close()


def test_an_owed_row_reads_in_full_on_a_phone(page):
    now(page)
    size = page.viewport_size
    page.evaluate("""() => {
        state.model.findings = [{ sweep: 'owner', rows: [{ text: 'Run it?, Which way?: Yes', at: '2026-11-01T01:00:00Z', owner: 'ci-speed-1' }] }];
        state.model.coordinator_overdue = [{ id: 'ask-1', group: 'ask', due: '3:14 AM', text: 'Cut the job', holder: 'ci-speed-1' }];
        state.model.owner_asks = { messages: [], taps: [], store: [] };
        state.model.replies = [];
        state.model.chat_lines = [];
        state.model.board.answered = {};
        location.hash = '#/replies';
    }""")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".ct-owed > summary").click()
    side = page.locator(".ct-owed .ev-side").last
    assert side.text_content().strip()
    assert side.evaluate("(el) => el.scrollWidth <= el.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.evaluate("() => { state.model.coordinator_overdue = []; state.model.findings = []; delete state.model.owner_asks; render(); }")
    page.set_viewport_size(size)
    now(page)


def test_a_phone_keeps_chat_clear_until_he_writes_and_reply_send_above_the_new_ask_box(page):
    context = page.context.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    phone = context.new_page()
    phone.goto(page.url.split("#")[0] + "#/")
    news = {"reply": {"id": "news", "title": "An update", "md": "What moved", "type": "update", "status": "progress"}, "at": "2026-11-03T13:00:00Z", "surfaces": []}
    chat_with(phone, [news], [ask(3, "Delivered")])
    heading = phone.locator(".page-heading").bounding_box()
    assert phone.locator("#cw-list").bounding_box()["y"] - heading["y"] <= 170
    tops = [chip.bounding_box()["y"] for chip in phone.locator(".filter").all()]
    assert max(tops) - min(tops) <= 1
    assert phone.locator(".filter small:visible").count() == 1
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390
    composer = phone.locator(".composer")
    send = phone.locator(".compose-bottom button")
    assert composer.bounding_box()["height"] <= 60
    assert not send.is_visible()
    box = phone.locator(".composer textarea")
    box.tap()
    assert composer.bounding_box()["height"] >= 120
    assert send.is_visible()
    box.fill("a draft")
    phone.evaluate("document.activeElement.blur()")
    assert send.is_visible()
    box.fill("")
    phone.evaluate("document.activeElement.blur()")
    phone.locator(".obj-reply").first.tap()
    sent = phone.locator(".decision-card .reply-editor button")
    sent.wait_for()
    assert sent.bounding_box()["y"] + sent.bounding_box()["height"] <= composer.bounding_box()["y"]
    context.close()


def test_a_note_with_no_message_above_it_is_not_open_work_and_only_an_undelivered_order_is(page):
    news = {"reply": {"id": "news", "title": "9:00 PM update", "md": "What moved", "type": "update", "status": "progress"}, "at": "2026-11-03T13:00:00Z", "surfaces": []}
    chat_with(page, [news], [
        ask(3, "Nobody took this", owed={"id": "m3", "status": "open", "lane": "", "hold": ""}),
        ask(4, "Last with a worker", owed={"id": "m4", "status": "in progress", "lane": "tap-rows-1", "hold": ""}),
        ask(5, "Delivered"),
    ])
    assert page.locator('[data-cw-filter="progress"] small').inner_text() == "2"
    assert page.locator('[data-cw-filter="completed"] small').inner_text() == "1"
    assert page.locator(".thread.loose .pill").count() == 0
    asks = page.locator(".cw .thread:has(.ask-head)")
    pills = {asks.nth(i).locator(".ask-title").inner_text(): asks.nth(i).locator(".pill").inner_text() for i in range(asks.count())}
    assert pills == {"Nobody took this": "In progress", "Last with a worker": "In progress", "Delivered": "Completed"}
    nobody = page.locator(".cw .thread:has(.ask-title:text-is('Nobody took this'))")
    nobody.locator(".ask-head").click()
    assert "Nobody has it" in nobody.locator(".ask-detail").inner_text()
    page.evaluate("cw.open.clear()")
    now(page)


def test_an_answer_wears_the_type_it_records_not_its_status(page):
    typed = {"reply": {"id": "typed", "title": "Plainly typed", "md": "The words", "type": "done", "status": "info"}, "at": "2026-11-03T13:00:00Z", "surfaces": []}
    chat_with(page, [typed], [])
    assert page.locator("#rp-typed .artifact-top").inner_text().split("\n")[0].strip() == "DONE"
    now(page)


def test_a_diagram_draws_its_own_card_with_no_one_pager(page):
    drawn = {"reply": {"id": "dia", "title": "How the relay runs", "md": "One picture.", "type": "explanation", "diagram": {"arch": 1, "name": "relay", "title": "The relay", "nodes": []}}, "at": "2026-11-03T13:00:00Z", "surfaces": []}
    chat_with(page, [drawn], [])
    card = page.locator(".cw .document-card")
    assert card.locator(".artifact-top").inner_text().strip() == "DIAGRAM"
    assert card.locator(".document-title").inner_text() == "The relay"
    now(page)
