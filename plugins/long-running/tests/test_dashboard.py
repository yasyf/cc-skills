import html
import http.client
import json
import re
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from drive import dashboard, picture

NET = dashboard.Tailnet("box", "box.tail1.ts.net", ["100.64.0.1", "fd7a::1"], "owner@example.com", False)


def test_links_lead_with_https_on_the_tailnet_name_when_the_tailnet_issues_certificates_else_the_bare_label() -> None:
    assert dashboard.links_of(41330, NET._replace(https=True)) == ["https://box.tail1.ts.net:41330/", "http://127.0.0.1:41330/"]
    assert dashboard.links_of(41330, NET) == ["http://box:41330/", "http://127.0.0.1:41330/"]
    assert dashboard.links_of(41330, None) == ["http://127.0.0.1:41330/"]


def serving(board: dashboard.Board, net: dashboard.Tailnet = NET) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer((dashboard.LOOPBACK, 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = dashboard.handler(port, net, board)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


@pytest.mark.parametrize("https", [False, True])
def test_page_answers_its_own_tailnet_names_and_refuses_any_other_host(https: bool) -> None:
    board = dashboard.Board("demo", [])
    board.version, board.body = 1, b"{}"
    server, port = serving(board, NET._replace(https=https))
    statuses = {}
    for host in ("127.0.0.1", "box", "100.64.0.1", "[fd7a::1]", "box.tail1.ts.net", "evil.example"):
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", "/api/view", headers={"Host": f"{host}:{port}"})
        statuses[host] = connection.getresponse().status
        connection.close()
    server.shutdown()
    assert statuses == {
        "127.0.0.1": 200,
        "box": 200,
        "100.64.0.1": 200,
        "[fd7a::1]": 200,
        "box.tail1.ts.net": 200 if https else 403,
        "evil.example": 403,
    }


def post_answer(port: int, origin: str, card: str, label: str) -> tuple[int, dict]:
    return post(port, origin, "/api/answer", {"card": card, "label": label})


def post(port: int, origin: str, path: str, asked: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
    body = json.dumps(asked)
    connection.request("POST", path, body=body, headers={"Host": f"box:{port}", "Origin": origin, "Content-Type": "application/json"})
    reply = connection.getresponse()
    said = reply.read()
    connection.close()
    return reply.status, json.loads(said) if said.startswith(b"{") else {}


def test_an_answer_reaches_cc_slack_only_from_the_page_itself_for_a_card_still_waiting_on_that_option(monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D1/p100"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Approve", "Hold"]}
    asked = []
    monkeypatch.setattr(dashboard, "answer_card", lambda card, label, via: asked.append((card, label, via)) or {"answered": True, "label": label})
    monkeypatch.setattr(dashboard.view, "refresh_cards", lambda name: None)
    monkeypatch.setattr(dashboard, "UNDO_SECONDS", 0)
    server, port = serving(board)
    elsewhere = post_answer(port, "https://evil.example", card, "Approve")
    gone = post_answer(port, f"http://box:{port}", card, "Merge")
    taken = post_answer(port, f"http://box:{port}", card, "Approve")
    server.shutdown()
    assert elsewhere[0] == 403
    assert gone == (409, {"error": dashboard.GONE})
    assert taken == (200, {"answered": True, "label": "Approve"})
    assert asked == [(card, "Approve", f"http://box:{port}/")]


def test_an_answer_undone_while_held_never_reaches_cc_slack_and_one_already_sent_cannot_be_undone(monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D1/p100"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Approve", "Hold"]}
    asked = []
    monkeypatch.setattr(dashboard, "answer_card", lambda card, label, via: asked.append((card, label)) or {"answered": True, "label": label})
    monkeypatch.setattr(dashboard.view, "refresh_cards", lambda name: None)
    monkeypatch.setattr(dashboard, "UNDO_SECONDS", 2)
    server, port = serving(board)
    page = f"http://box:{port}"
    held = {}
    clicked = threading.Thread(target=lambda: held.update(reply=post_answer(port, page, card, "Approve")))
    clicked.start()
    while card not in board.held:
        time.sleep(0.01)
    twice = post_answer(port, page, card, "Hold")
    undone = post(port, page, "/api/undo", {"card": card})
    clicked.join()
    monkeypatch.setattr(dashboard, "UNDO_SECONDS", 0)
    sent = post_answer(port, page, card, "Hold")
    late = post(port, page, "/api/undo", {"card": card})
    server.shutdown()
    assert twice == (409, {"error": dashboard.HELD})
    assert undone == (200, {"undone": True})
    assert held["reply"] == (200, {"undone": True})
    assert sent == (200, {"answered": True, "label": "Hold"})
    assert late == (409, {"error": dashboard.SENT})
    assert asked == [(card, "Hold")]


WEB_RUNNER = """
import { readFileSync } from "node:fs";
import vm from "node:vm";
const quiet = { addEventListener() {} };
const context = vm.createContext({ document: quiet, window: quiet, components: JSON.parse(readFileSync(0, "utf8")) });
console.log(vm.runInContext(readFileSync(process.argv[2], "utf8") + "\\n" + process.argv[3], context));
"""
WEB_RENDER = r"""
model = { repo: "", review: [], visuals: Object.fromEntries(components.map(([kind, source]) => ["/drive/pictures/" + kind + "/show-me.png", { kind, source }])) };
JSON.stringify(components.map(([kind, source, fence]) => {
  const picture = "/drive/pictures/" + kind + "/show-me.png";
  return [kind, {
    fence: prose("```" + fence + "\n" + source + "\n```"),
    card: prose("Show me\n![Show me](" + picture + ")"),
    pr: pictures([picture]),
    said: saidRow({ at: "2026-11-10T23:00:00Z", text: "", visuals: [{ kind, source, caption: "", picture }], prs: [] }),
  }];
}));
"""
COMPONENTS = [
    ("code:go", "x := 1", "go"),
    ("mermaid", "flowchart LR\n  run --> server", "mermaid"),
    ("dot", "digraph { run -> server }", "dot"),
    ("chart", '{"mark": "bar", "data": {"values": [{"x": "a", "y": 1}]}}', "chart"),
    ("compare", '{"options": [{"name": "Two threads", "points": ["+ least load"]}]}', "compare"),
    ("arch", '{"nodes": [{"id": "run", "kind": "service", "name": "run"}]}', "arch"),
]


def web(tmp_path: Path, script: str, given: object = None) -> object:
    runner = tmp_path / "web.mjs"
    runner.write_text(WEB_RUNNER)
    app = Path(dashboard.__file__).parent / "site" / "app.js"
    return json.loads(subprocess.run(["node", str(runner), str(app), script], input=json.dumps(given), capture_output=True, text=True, check=True).stdout)


def test_the_web_draws_every_component_kind_natively_and_never_as_a_picture(tmp_path: Path) -> None:
    for kind, surfaces in web(tmp_path, WEB_RENDER, COMPONENTS):
        for surface, shown in surfaces.items():
            assert "<img" not in shown, f"the web drew the {kind} on its {surface} surface as a picture: {shown}"
            assert f'data-visual="{kind}"' in shown, f"the web drew no native {kind} on its {surface} surface: {shown}"


STACK_RENDER = r"""
model = { repo: "", review: [] };
const pr = (number, state, tap) => ({ title: "change " + number, url: "https://github.com/yasyf/cc-skills/pull/" + number, state, checks: "pass", review: "approved", tap });
const row = (prs) => saidRow({ at: "2026-11-10T23:00:00Z", text: "land these", visuals: [], prs });
JSON.stringify([row([pr(1, "open", "wanted"), pr(2, "open", "wanted")]), row([pr(1, "merged", "Approve")])]);
"""


def test_a_stack_card_sent_to_him_lists_its_prs_and_links_his_tap_only_while_one_waits(tmp_path: Path) -> None:
    waiting, landed = web(tmp_path, STACK_RENDER, [])
    assert waiting.index("/pull/1") < waiting.index("/pull/2")
    assert 'href="#/queue/tap"' in waiting
    assert "/pull/1" in landed and 'href="#/queue/tap"' not in landed


def test_the_screens_are_the_four_he_kept(tmp_path: Path) -> None:
    assert web(tmp_path, "JSON.stringify(SCREENS.map(([name]) => name))") == ["queue", "now", "work", "history"]


DIAGRAM_CLASSES = {
    "graph", "node", "edge", "cluster",
    "root", "nodes", "clusters", "edgePaths", "edgePath", "edgeLabels", "edgeLabel", "label", "nodeLabel",
    "cluster-label", "labelBkg", "flowchart-link", "marker", "default", "actor", "note", "messageText", "labelBox",
    "marks", "background",
}


def test_the_dashboard_styles_no_class_a_diagram_draws_with_so_a_diagram_lays_out_as_it_does_in_slack() -> None:
    css = (Path(dashboard.__file__).parent / "site" / "app.css").read_text()
    styled = {name for selector in re.findall(r"([^{}]+)\{", css) for name in re.findall(r"\.(-?[A-Za-z_][\w-]*)", selector)}
    assert not styled & DIAGRAM_CLASSES


def test_a_picture_is_served_only_when_a_shown_card_draws_it(tmp_path: Path) -> None:
    drawn, other = tmp_path / "drawn.png", tmp_path / "other.png"
    drawn.write_bytes(b"png")
    other.write_bytes(b"secret")
    board = dashboard.Board("demo", [])
    board.pictures = {str(drawn)}
    server, port = serving(board)
    statuses = {}
    for path in (drawn, other):
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", f"/api/picture?path={urllib.parse.quote(str(path))}", headers={"Host": f"box:{port}"})
        reply = connection.getresponse()
        statuses[path.name] = (reply.status, reply.read() if reply.status == 200 else b"")
        connection.close()
    server.shutdown()
    assert statuses == {"drawn.png": (200, b"png"), "other.png": (404, b"")}


def test_a_thread_is_read_only_for_an_ask_of_this_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    board = dashboard.Board("demo", [])
    board.threads, board.channel = {"1794257898.401849"}, "D123"
    read = []
    monkeypatch.setattr(dashboard, "cc_slack", lambda *args: read.append(args) or {"messages": []})
    server, port = serving(board)
    statuses = {}
    for ts in ("1794257898.401849", "1794000000.000001"):
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", f"/api/thread/{ts}", headers={"Host": f"box:{port}"})
        statuses[ts] = connection.getresponse().status
        connection.close()
    server.shutdown()
    assert statuses == {"1794257898.401849": 200, "1794000000.000001": 404}
    assert read == [("thread", "--channel", "D123", "--thread", "1794257898.401849", "--limit", dashboard.THREAD_MESSAGES)]


def test_cited_code_is_read_at_its_prs_head_or_the_drives_default_branch_and_only_from_those(monkeypatch: pytest.MonkeyPatch) -> None:
    board = dashboard.Board("demo", [])
    board.heads, board.repo = {"yasyf/cc-skills#41": "abc123"}, "Forge-AI/monorepo"
    read = []
    monkeypatch.setattr(dashboard.github, "run", lambda cmd: read.append(cmd[2]) or "one\ntwo\nthree\n")
    server, port = serving(board)
    replies = {}
    for cite, path in (("yasyf/cc-skills#41", "a/b.py"), ("Forge-AI/monorepo", "go/c.go"), ("yasyf/other", "a/b.py"), ("Forge-AI/monorepo", "../etc/passwd")):
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", f"/api/source?{urllib.parse.urlencode({'cite': cite, 'path': path, 'line': 2, 'to': 2})}", headers={"Host": f"box:{port}"})
        reply = connection.getresponse()
        replies[(cite, path)] = (reply.status, json.loads(reply.read()).get("lines"))
        connection.close()
    server.shutdown()
    window = [{"n": 1, "text": "one"}, {"n": 2, "text": "two"}, {"n": 3, "text": "three"}, {"n": 4, "text": ""}]
    assert replies == {
        ("yasyf/cc-skills#41", "a/b.py"): (200, window),
        ("Forge-AI/monorepo", "go/c.go"): (200, window),
        ("yasyf/other", "a/b.py"): (404, None),
        ("Forge-AI/monorepo", "../etc/passwd"): (400, None),
    }
    assert read == ["repos/yasyf/cc-skills/contents/a/b.py?ref=abc123", "repos/Forge-AI/monorepo/contents/go/c.go"]


def test_typed_words_reach_cc_slack_only_from_the_page_itself_and_only_under_a_card_thread_of_this_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D1/p100"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Go", "Hold"]}
    relayed = []
    monkeypatch.setattr(dashboard, "relay", lambda text, under: relayed.append((text, under)) or {"posted": True, "permalink": f"{under or card}#copy"})
    server, port = serving(board)
    page = f"http://box:{port}"
    elsewhere = post(port, "https://evil.example", "/api/say", {"text": "merge it", "under": card})
    stranger = post(port, page, "/api/say", {"text": "merge it", "under": "https://acme.slack.com/archives/D1/p999"})
    blank = post(port, page, "/api/say", {"text": "  ", "under": card})
    replied = post(port, page, "/api/say", {"text": " hold #41 until the stack lands\n", "under": card})
    asked = post(port, page, "/api/say", {"text": "ship the dashboard", "under": ""})
    server.shutdown()
    assert elsewhere[0] == 403
    assert stranger == (409, {"error": dashboard.ELSEWHERE})
    assert blank[0] == 400
    assert replied == (200, {"posted": True, "permalink": f"{card}#copy"})
    assert asked[0] == 200
    assert relayed == [("hold #41 until the stack lands", card), ("ship the dashboard", "")]


def test_a_review_opens_only_from_the_page_itself_for_an_open_pr_of_this_drive_at_its_head(monkeypatch: pytest.MonkeyPatch) -> None:
    board = dashboard.Board("demo", [])
    board.heads = {"yasyf/cc-skills#41": "abc123"}
    opened = []
    monkeypatch.setattr(board.assistant, "url", lambda key, head: opened.append((key, head)) or "https://box.tail1.ts.net:5/s/r41")
    server, port = serving(board)
    page = f"http://box:{port}"
    elsewhere = post(port, "https://evil.example", "/api/review/yasyf/cc-skills/41", {})
    stranger = post(port, page, "/api/review/yasyf/cc-skills/42", {})
    shown = post(port, page, "/api/review/yasyf/cc-skills/41", {})
    server.shutdown()
    assert elsewhere[0] == 403
    assert stranger[0] == 404
    assert shown == (200, {"url": "https://box.tail1.ts.net:5/s/r41"})
    assert opened == [("yasyf/cc-skills#41", "abc123")]


SITE = Path(dashboard.__file__).parent / "site"
RESULT = re.compile(r'<html[^>]*\bdata-result="([^"]*)"')
FEATURE = {
    "id": "o1", "title": "ship the fix", "state": "running", "now": "", "project": "Platform", "workstream": "SandSQL", "due": None, "late": False,
    "proof": None, "updated": "2026-11-11T00:00:00-08:00", "url": None, "prs": [], "built": [], "cards": [], "text": "ship the fix", "note": "",
    "created": "2026-11-10T20:00:00-08:00", "jobs": [],
}

QUESTION = {
    "kind": "question", "answer": "https://x.slack.com/archives/D1/p1794382453859209", "title": "Raise the limit?", "since": None, "holds": 0, "blocks": [], "thread": None,
    "card": {"title": "Raise the limit?", "body": "", "options": ["Raise it", "Shard it"], "recommended": "Raise it"},
}


def board(**changed: object) -> dict:
    lanes = [
        {"id": f"j{n}", "feature": "o1", "kind": "builder", "goal": f"lane {n}", "state": "running", "where": "local", "said": "", "updated": "2026-11-11T00:00:00-08:00", "prs": 0, "branch": None, "waits": 0, "files": []}
        for n in range(40)
    ]
    moments = [
        {"at": f"2026-11-10T{23 - n // 60:02d}:{59 - n % 60:02d}:00-08:00", "kind": "merged", "text": f"change {n}", "detail": "", "url": "https://github.com/a/b/pull/1", "feature": "o1"}
        for n in range(120)
    ]
    counts = {"queue": 0, "blocked": 0, "running": 1, "built": 0, "waiting": 0, "merged": 0, "live": 0, "not started": 0}
    return {
        "drive": "demo", "at": "2026-11-11T00:00:00-08:00", "counts": counts, "queue": [], "status": [{"state": "running", "features": [FEATURE]}], "cards": "live",
        "review": [], "work": lanes, "history": moments, "incidents": [], "visuals": {}, "repo": "", "undo_seconds": 10, "links": [], "channel": "D1",
    } | changed


def browse(tmp_path: Path, steps: str, model: dict, size: str = "1200,600", *flags: str) -> object:
    """Load the dashboard page in headless Chrome on a stub stream that sends model, run steps (an async module that may call push(model) and
    wait(ms)), and return what steps put in done(result)."""
    harness = f"""<script>
const MODEL = {json.dumps(model)};
window.EventSource = class {{ constructor() {{ window.STREAM = this; setTimeout(() => this.onmessage({{ data: JSON.stringify(MODEL) }}), 0); }} }};
const wait = (ms) => new Promise((done) => setTimeout(done, ms));
const push = async (model) => {{ window.STREAM.onmessage({{ data: JSON.stringify(model) }}); await wait(50); }};
const done = (result) => {{ document.documentElement.dataset.result = JSON.stringify(result); }};
</script>"""
    page = (SITE / "index.html").read_text()
    page = page.replace('<link rel="stylesheet" href="/app.css">', f"<style>{(SITE / 'app.css').read_text()}</style>")
    page = page.replace('<script src="/app.js"></script>', f"{harness}<script>{(SITE / 'app.js').read_text()}</script><script type=\"module\">await wait(100);\n{steps}</script>")
    (tmp_path / "page.html").write_text(page)
    dumped = picture.headless(tmp_path / "page.html", f"--window-size={size}", "--no-sandbox", *flags, "--dump-dom")
    return json.loads(html.unescape(RESULT.search(dumped)[1]))


def test_a_live_update_keeps_the_page_he_is_on_in_place(tmp_path: Path) -> None:
    kept = browse(tmp_path, """
location.hash = '#/work/Platform/SandSQL';
await wait(100);
const cards = document.querySelector('#main .lane-cards');
cards.scrollTop = 300;
await push({ ...MODEL, at: '2026-11-11T00:05:00-08:00', work: MODEL.work.map((lane) => ({ ...lane, said: 'moved on' })) });
done({ same: document.querySelector('#main .lane-cards') === cards, scrolled: cards.scrollTop, said: document.querySelector('.lane-said').textContent });
""", board())
    assert kept == {"same": True, "scrolled": 300, "said": "moved on"}


def test_back_returns_to_where_he_was_and_the_history_filter_lives_in_the_link(tmp_path: Path) -> None:
    walked = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
window.scrollTo(0, 900);
await wait(300);
document.querySelector('.subtabs a[href$="show=prs"]').click();
await wait(100);
const filtered = { hash: location.hash, top: scrollY, on: document.querySelector('.subtabs a.on').textContent };
document.querySelector('.tab[href="#/now"]').click();
await wait(100);
history.back();
await wait(100);
const back = { hash: location.hash, top: scrollY };
history.back();
await wait(100);
done({ filtered, back, first: { hash: location.hash, top: scrollY } });
""", board())
    assert walked == {
        "filtered": {"hash": "#/history?show=prs", "top": 0, "on": "PRs merged"},
        "back": {"hash": "#/history?show=prs", "top": 0},
        "first": {"hash": "#/history", "top": 900},
    }


def test_a_request_that_fails_says_so_where_he_is_looking_instead_of_hanging(tmp_path: Path) -> None:
    shown = browse(tmp_path, """
window.fetch = () => Promise.reject(new Error('offline'));
openPR('a/b#7');
await wait(100);
const drawer = document.querySelector('#drawer .refused')?.textContent;
toast('card', { label: 'Approve', title: 'ship it', until: Date.now() + 10000, state: 'held' });
await undo('card');
done({ drawer, toast: document.querySelector('#toasts .toast .meta').textContent });
""", board(review=[{"key": "a/b#7", "title": "change 7", "url": "https://github.com/a/b/pull/7", "head": "abc"}]))
    assert shown == {
        "drawer": "Could not reach the dashboard server (offline).",
        "toast": "Could not reach the dashboard server (offline).",
    }


def test_a_figure_is_drawn_once_and_one_that_cannot_draw_shows_what_it_is_drawn_from(tmp_path: Path) -> None:
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "the flow", "detail": "", "url": None, "feature": "o1", "visuals": [{"kind": "mermaid", "source": "flowchart LR", "caption": "", "picture": "/p.png"}], "prs": []}
    shown = browse(tmp_path, """
location.hash = '#/history';
await wait(200);
const drawn = document.querySelector('#main .visual-host pre');
await push({ ...MODEL, at: '2026-11-11T00:05:00-08:00' });
done({ source: drawn.textContent, same: document.querySelector('#main .visual-host pre') === drawn });
""", board(history=[said]))
    assert shown == {"source": "flowchart LR", "same": True}


def test_a_picture_opens_in_a_lightbox_in_place_and_steps_to_the_next_one_on_the_same_card(tmp_path: Path) -> None:
    pictures = [{"kind": "image", "source": "", "caption": caption, "picture": f"/{caption}.png"} for caption in ("before", "after")]
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "two shots", "detail": "", "url": None, "feature": "o1", "visuals": pictures, "prs": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
const first = document.querySelector('#main [data-shot]');
first.focus();
first.click();
await wait(50);
const box = document.querySelector('#lightbox');
const opened = { open: box.open, hash: location.hash, shown: box.querySelector('.lb-stage img').getAttribute('src'), caption: box.querySelector('.lb-caption').textContent, count: box.querySelector('.lb-bar .meta').textContent };
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
await wait(50);
const stepped = { shown: box.querySelector('.lb-stage img').getAttribute('src'), count: box.querySelector('.lb-bar .meta').textContent };
box.querySelector('[data-close]').click();
await wait(50);
done({ opened, stepped, closed: !box.open, focus: document.activeElement === first });
""", board(history=[said]))
    assert seen == {
        "opened": {"open": True, "hash": "#/history", "shown": "/api/picture?path=%2Fbefore.png", "caption": "before", "count": "1 of 2"},
        "stepped": {"shown": "/api/picture?path=%2Fafter.png", "count": "2 of 2"},
        "closed": True,
        "focus": True,
    }


def test_every_figure_enlarges_into_the_lightbox_drawn_from_the_same_source(tmp_path: Path) -> None:
    figure = {"kind": "mermaid", "source": "flowchart LR\n  run --> server", "caption": "the flow", "picture": "/flow.png"}
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "a flow", "detail": "", "url": None, "feature": "o1", "visuals": [figure], "prs": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
document.querySelector('#main [data-enlarge]').click();
await wait(50);
const host = document.querySelector('#lightbox .visual-host');
done({ open: document.querySelector('#lightbox').open, kind: host.dataset.visual, source: host.dataset.source, caption: document.querySelector('#lightbox .lb-caption').textContent });
""", board(history=[said]))
    assert seen == {"open": True, "kind": "mermaid", "source": "flowchart LR\n  run --> server", "caption": "the flow"}


def test_moving_focus_into_a_folded_summary_opens_it_and_its_picture_opens_in_the_lightbox(tmp_path: Path) -> None:
    body = "\n\n".join(f"Reason {n} for the pick." for n in range(12)) + "\n\n![the lookup](/lookup.png)"
    item = {
        "kind": "question", "answer": "https://x.slack.com/archives/D1/p1794382453859209", "title": "Pick one", "since": None, "holds": 0, "blocks": [], "thread": None,
        "card": {"title": "Pick one", "body": body, "options": ["Keep it", "Drop it"], "recommended": "Keep it"},
    }
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const fold = document.querySelector('#main .fold');
const before = { long: fold.classList.contains('long'), open: fold.classList.contains('open') };
fold.querySelector('[data-shot]').focus();
await wait(50);
const after = { open: document.querySelector('#main .fold').classList.contains('open'), focused: document.activeElement.matches('[data-shot]') };
document.activeElement.click();
await wait(50);
done({ before, after, lightbox: document.querySelector('#lightbox').open, hash: location.hash });
""", board(queue=[item]))
    assert seen == {"before": {"long": True, "open": False}, "after": {"open": True, "focused": True}, "lightbox": True, "hash": "#/queue/all/1794382453.859209"}


def test_a_click_on_a_picture_inside_a_folded_summary_opens_the_lightbox_and_leaves_the_fold_as_it_was(tmp_path: Path) -> None:
    body = "\n\n".join(f"Reason {n} for the pick." for n in range(12)) + "\n\n![the lookup](/lookup.png)"
    item = {
        "kind": "question", "answer": "https://x.slack.com/archives/D1/p1794382453859209", "title": "Pick one", "since": None, "holds": 0, "blocks": [], "thread": None,
        "card": {"title": "Pick one", "body": body, "options": ["Keep it", "Drop it"], "recommended": "Keep it"},
    }
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const shot = document.querySelector('#main .fold [data-shot]');
shot.focus({ focusVisible: false });
await wait(50);
shot.click();
await wait(50);
done({ open: document.querySelector('#main .fold').classList.contains('open'), lightbox: document.querySelector('#lightbox').open });
""", board(queue=[item]))
    assert seen == {"open": False, "lightbox": True}


def test_the_top_strip_counts_open_incidents_and_lists_them_waiting_on_him_first(tmp_path: Path) -> None:
    quiet = FEATURE | {"id": "o1", "title": "Mac app cannot read chats", "updated": "2026-11-10T21:00:00-08:00"}
    asking = FEATURE | {"id": "o2", "title": "SandSQL journal failing", "cards": [{"title": "Raise the limit?", "url": "https://x.slack.com/archives/D1/p1794382453859209", "asked": "2026-11-10T23:00:00-08:00"}]}
    pr = {"title": "fix", "url": "https://github.com/a/b/pull/3", "state": "open", "checks": "pending", "review": "none", "tap": None}
    moving = FEATURE | {"id": "o3", "title": "Env restore copies at 0 B/s", "jobs": [{"id": "j3", "state": "running"}], "prs": [pr]}
    incidents = [{"id": entry["id"], "title": entry["title"], "created": "2026-11-10T20:00:00-08:00"} for entry in (quiet, asking, moving)]
    seen = browse(tmp_path, """
const strip = [...document.querySelectorAll('#alerts > *')].map((each) => each.textContent);
document.querySelector('#alerts [popovertarget]').click();
await wait(50);
const list = document.querySelector('#incidents');
const opened = list.matches(':popover-open');
const rows = [...list.querySelectorAll('li a')].map((row) => [row.getAttribute('href'), row.querySelector('.meta').textContent.replace(/\\s+/g, ' ').split(' · ').slice(1)]);
list.querySelector('li a').click();
await wait(50);
done({ strip, opened, rows, closed: !list.matches(':popover-open'), hash: location.hash });
""", board(status=[{"state": "running", "features": [quiet, asking, moving]}], incidents=incidents, queue=[QUESTION]))
    assert seen == {
        "strip": ["3 incidents open", "1 decision for you"],
        "opened": True,
        "rows": [
            ["#/feature/o2", ["waits on your answer"]],
            ["#/feature/o3", ["1 job working", "1 open PR"]],
            ["#/feature/o1", ["nothing moving since Nov 10 9:00 PM"]],
        ],
        "closed": True,
        "hash": "#/feature/o2",
    }


def test_the_top_strip_shows_on_a_phone(tmp_path: Path) -> None:
    incidents = [{"id": "o1", "title": "ship the fix", "created": "2026-11-10T20:00:00-08:00"}]
    seen = browse(tmp_path, """
const chips = [...document.querySelectorAll('#alerts > *')].map((chip) => chip.getBoundingClientRect());
done({ shown: chips.length, inside: chips.every((chip) => chip.width > 0 && chip.left >= 0 && chip.right <= innerWidth) });
""", board(incidents=incidents, queue=[QUESTION, QUESTION | {"kind": "tap", "answer": "https://x.slack.com/archives/D1/p1794382453859300"}]), size="390,844")
    assert seen == {"shown": 3, "inside": True}


def test_now_zooms_from_projects_to_a_task_inside_one_canvas_without_leaving_his_place(tmp_path: Path) -> None:
    iris = FEATURE | {"id": "o2", "title": "chats open on the Mac", "project": "Iris", "workstream": "Desktop app"}
    seen = browse(tmp_path, """
location.hash = '#/now';
await wait(150);
window.scrollTo(0, 120);
await wait(200);
const canvas = document.querySelector('#main .canvas');
const world = canvas.querySelector('.world');
const look = () => ({ hash: location.hash, level: canvas.dataset.level, same: document.querySelector('#main .canvas') === canvas, top: scrollY, scale: Number(world.style.getPropertyValue('--k')) });
const overview = look();
canvas.querySelector('.region[data-cam-key="Platform"]').click();
await wait(800);
const project = look();
canvas.querySelector('.stream[data-cam-key="Platform/SandSQL"]').click();
await wait(800);
const stream = look();
canvas.querySelector('.stream.here a.task-row').click();
await wait(300);
const task = { ...look(), sheet: canvas.querySelector('.task-sheet h2').textContent };
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
await wait(300);
done({ overview, project, stream, task, closed: { ...look(), sheet: !!canvas.querySelector('.task-sheet') } });
""", board(status=[{"state": "running", "features": [FEATURE, iris]}]), "1200,600", "--force-prefers-reduced-motion")
    assert {name: {key: value for key, value in view.items() if key != "scale"} for name, view in seen.items()} == {
        "overview": {"hash": "#/now", "level": "all", "same": True, "top": 120},
        "project": {"hash": "#/now/Platform", "level": "project", "same": True, "top": 120},
        "stream": {"hash": "#/now/Platform/SandSQL", "level": "stream", "same": True, "top": 120},
        "task": {"hash": "#/now/Platform/SandSQL/o1", "level": "stream", "same": True, "top": 120, "sheet": "ship the fix"},
        "closed": {"hash": "#/now/Platform/SandSQL", "level": "stream", "same": True, "top": 120, "sheet": False},
    }
    assert seen["overview"]["scale"] < seen["project"]["scale"] < seen["stream"]["scale"]


def test_a_card_link_opens_that_card_and_the_queue_keeps_its_place_in_the_address(tmp_path: Path) -> None:
    second = QUESTION | {"answer": "https://x.slack.com/archives/D1/p1794382453859300", "title": "Shard it?", "card": QUESTION["card"] | {"title": "Shard it?"}}
    seen = browse(tmp_path, """
location.hash = '#/queue/question/1794382453.859300';
await wait(100);
const opened = { title: document.querySelector('#item-title').textContent, hash: location.hash };
document.querySelector('#main [data-move="-1"]').click();
await wait(50);
done({ opened, moved: { title: document.querySelector('#item-title').textContent, hash: location.hash } });
""", board(queue=[QUESTION, second]))
    assert seen == {
        "opened": {"title": "Shard it?", "hash": "#/queue/question/1794382453.859300"},
        "moved": {"title": "Raise the limit?", "hash": "#/queue/question/1794382453.859209"},
    }
