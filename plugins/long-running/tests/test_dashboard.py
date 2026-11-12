import html
import http.client
import json
import re
import subprocess
import threading
import time
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self

import pytest
from drive import dashboard, picture, state, view
from test_view import drive_of, found, job, order, pr

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


def test_a_plain_http_link_to_the_tailnet_port_moves_to_https_on_the_full_name() -> None:
    server = dashboard.Server(dashboard.LOOPBACK, 0, BaseHTTPRequestHandler, types.SimpleNamespace(name="box.tail1.ts.net"))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
    connection.request("GET", "/api/view", headers={"Host": f"box:{port}"})
    reply = connection.getresponse()
    connection.close()
    server.shutdown()
    assert (reply.status, reply.getheader("Location")) == (301, f"https://box.tail1.ts.net:{port}/api/view")


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


def test_a_dashboard_answer_cut_off_as_it_stops_reads_as_nothing_serving_so_a_restart_waits_on(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stopping:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, *_: object) -> bytes:
            raise http.client.IncompleteRead(b"", 1779760)

    monkeypatch.setattr(dashboard.urllib.request, "urlopen", lambda url, timeout: Stopping())
    assert dashboard.serving("demo", 41330) is None

def test_an_answer_reaches_cc_slack_only_from_the_page_itself_for_a_card_still_waiting_on_that_option(monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D1/p100"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Approve", "Hold"]}
    board.revisions = {card: ("Fix: approve web search?", [])}
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
    board.revisions = {card: ("Fix: approve web search?", [])}
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


def test_an_answer_held_while_its_card_is_drawn_again_for_another_pr_head_never_reaches_cc_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D1/p100"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Approve", "Hold"]}
    board.revisions = {card: dashboard.revision({"title": "Fix: approve web search?", "prs": [{"head": "aaa1111"}]})}
    asked = []
    monkeypatch.setattr(dashboard, "answer_card", lambda card, label, via: asked.append((card, label)) or {"answered": True, "label": label})
    monkeypatch.setattr(dashboard.view, "refresh_cards", lambda name: None)
    monkeypatch.setattr(dashboard, "UNDO_SECONDS", 2)
    server, port = serving(board)
    held = {}
    clicked = threading.Thread(target=lambda: held.update(reply=post_answer(port, f"http://box:{port}", card, "Approve")))
    clicked.start()
    while card not in board.held:
        time.sleep(0.01)
    board.revisions = {card: dashboard.revision({"title": "Fix: approve web search?", "prs": [{"head": "bbb2222"}]})}
    clicked.join()
    server.shutdown()
    assert held["reply"] == (409, {"error": dashboard.REDRAWN})
    assert asked == []


WEB_RUNNER = """
import { readFileSync } from "node:fs";
import vm from "node:vm";
const quiet = { addEventListener() {} };
const context = vm.createContext({ document: quiet, window: quiet, components: JSON.parse(readFileSync(0, "utf8")) });
console.log(vm.runInContext(readFileSync(process.argv[2], "utf8") + "\\n" + process.argv[3], context));
"""
WEB_RENDER = r"""
model = { repo: "", review: [] };
for (const [kind, source] of components) sources.set("/drive/pictures/" + kind + "/show-me.png", { kind, source });
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


def test_a_quoted_message_on_a_card_shows_as_one_quote_of_its_text_with_its_links_and_never_as_a_picture(tmp_path: Path) -> None:
    body = "\n".join(
        [
            "*Show me*",
            "The DM as drafted",
            "> Hey Rowan! The grant throws <https://sentry.io/1|this error> at 9:29 PM.",
            ">",
            "> Can you take it?",
            "After the quote.",
        ]
    )
    shown = web(tmp_path, f"model = {{ repo: '', review: [] }}; JSON.stringify(prose({json.dumps(body)}))")
    assert shown == (
        "<p><b>Show me</b></p><p>The DM as drafted</p>"
        '<blockquote class="quote">Hey Rowan! The grant throws <a href="https://sentry.io/1" target="_blank" rel="noopener">this error</a> at 9:29 PM.<br><br>Can you take it?</blockquote>'
        "<p>After the quote.</p>"
    )


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


def test_a_pr_the_drive_dropped_after_his_stack_card_shows_its_link_as_dropped_from_the_drive(tmp_path: Path) -> None:
    dropped = {"title": "https://github.com/Forge-AI/monorepo/pull/53080", "url": "https://github.com/Forge-AI/monorepo/pull/53080", "state": "dropped", "checks": None, "review": "none", "tap": None}
    shown = web(tmp_path, f"model = {{ repo: '', review: [] }}; JSON.stringify(prItem({json.dumps(dropped)}))")
    assert shown == '<li><a href="https://github.com/Forge-AI/monorepo/pull/53080" target="_blank" rel="noopener"><i class="sq dropped"></i>https://github.com/Forge-AI/monorepo/pull/53080</a><em>dropped from the drive</em></li>'


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
    monkeypatch.setattr(dashboard.github, "run", lambda cmd, **_: read.append(cmd[2]) or "one\ntwo\nthree\n")
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
        "review": [], "work": lanes, "history": moments, "incidents": [], "repo": "", "undo_seconds": 10, "links": [], "channel": "D1",
    } | changed


def browse(tmp_path: Path, steps: str, model: dict, size: str = "1200,600", *flags: str) -> object:
    """Serve the dashboard of model from the real handler and load it in headless Chrome with only its stream stubbed: the page reads the summary
    once, and push(model) shows the board model and hands the page its summary. Run steps, an async module that may call push(model) and wait(ms),
    and return what steps put in done(result)."""
    harness = f"""<script>
const MODEL = {json.dumps(model)};
window.EventSource = class {{ constructor() {{ window.STREAM = this; fetch('/api/view').then((reply) => reply.text()).then((data) => this.onmessage({{ data }})); }} }};
const wait = (ms) => new Promise((done) => setTimeout(done, ms));
const push = async (model) => {{ const reply = await fetch('/test/push', {{ method: 'POST', body: JSON.stringify(model) }}); window.STREAM.onmessage({{ data: await reply.text() }}); await wait(50); }};
const done = (result) => {{ document.documentElement.dataset.result = JSON.stringify(result); }};
</script>"""
    site = tmp_path / "site"
    site.mkdir(parents=True)
    page = (SITE / "index.html").read_text().replace('<script src="/app.js"></script>', f'{harness}<script src="/app.js"></script><script type="module">await wait(100);\n{steps}</script>')
    (site / "index.html").write_text(page)
    for name in ("app.js", "app.css"):
        (site / name).symlink_to(SITE / name)
    (site / "visuals.js").write_text("export function draw() { throw new Error('nothing draws in this test'); }\n")
    shown = dashboard.Board("demo", [])
    shown.show(model)
    server, port = serving(shown)

    class Pushed(server.RequestHandlerClass):
        def do_POST(self) -> None:
            if self.path != "/test/push":
                return super().do_POST()
            shown.show(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.reply(shown.after(0)[1], "application/json")

    server.RequestHandlerClass = Pushed
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(dashboard, "SITE", site)
        dumped = picture.headless(f"http://{dashboard.LOOPBACK}:{port}/", tmp_path / "profile", f"--window-size={size}", "--no-sandbox", *flags, "--dump-dom")
    server.shutdown()
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
""", board(review=[{"key": "a/b#7", "title": "change 7", "url": "https://github.com/a/b/pull/7", "head": "abc", "card": None}]))
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
const look = () => ({ hash: location.hash, level: canvas.dataset.level, same: document.querySelector('#main .canvas') === canvas, top: scrollY, shown: getComputedStyle(world).visibility === 'visible' && Number(world.style.getPropertyValue('--k')) === camera.k, scale: camera.k });
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
        "overview": {"hash": "#/now", "level": "all", "same": True, "top": 120, "shown": True},
        "project": {"hash": "#/now/Platform", "level": "project", "same": True, "top": 120, "shown": True},
        "stream": {"hash": "#/now/Platform/SandSQL", "level": "stream", "same": True, "top": 120, "shown": True},
        "task": {"hash": "#/now/Platform/SandSQL/o1", "level": "stream", "same": True, "top": 120, "shown": True, "sheet": "ship the fix"},
        "closed": {"hash": "#/now/Platform/SandSQL", "level": "stream", "same": True, "top": 120, "shown": True, "sheet": False},
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


def test_a_choice_he_only_hovered_lets_go_when_the_pointer_leaves(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const second = document.querySelectorAll('#main .choice')[1];
second.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await wait(50);
const hovered = { on: document.querySelector('#main .choice.on').textContent.trim().split(/\\s+/)[0], rail: document.querySelector('#main [data-means] .eyebrow').textContent };
document.querySelectorAll('#main .choice')[1].dispatchEvent(new MouseEvent('mouseout', { bubbles: true, relatedTarget: document.body }));
await wait(50);
done({ hovered, left: { on: document.querySelectorAll('#main .choice.on').length, rail: document.querySelector('#main [data-means] .eyebrow').textContent } });
""", board(queue=[QUESTION]))
    assert seen == {"hovered": {"on": "2", "rail": "Choice 2 of 2"}, "left": {"on": 0, "rail": "Recommended"}}


def test_arrow_keys_on_a_pr_cards_tabs_switch_tabs_and_leave_the_queue_where_it_is(tmp_path: Path) -> None:
    tap = {"kind": "tap", "url": "https://github.com/a/b/pull/7", "title": "fix the lock", "answer": None, "thread": None, "card": None, "since": None, "holds": 0, "blocks": []}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const tab = document.querySelector('#main [data-prtab="show"]');
tab.focus();
tab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
await wait(100);
done({ counter: document.querySelector('#main .counter span').textContent, selected: document.querySelector('#main [data-prtab][aria-selected="true"]').dataset.prtab, focused: document.activeElement.dataset.prtab });
""", board(queue=[tap, QUESTION]))
    assert seen == {"counter": "1 of 2", "selected": "context", "focused": "context"}


def test_escape_closes_the_ask_box_and_gives_focus_back_to_its_button(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
document.querySelector('#main [data-ask]').click();
await wait(50);
const typing = document.activeElement.matches('.ask-box textarea');
document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
await wait(50);
done({ typing, open: !!document.querySelector('#main .ask-box'), focus: document.activeElement.matches('[data-ask]'), hash: location.hash });
""", board(queue=[QUESTION]))
    assert seen == {"typing": True, "open": False, "focus": True, "hash": "#/queue/all/1794382453.859209"}


def test_a_long_card_link_in_history_wraps_on_a_phone_instead_of_widening_the_page(tmp_path: Path) -> None:
    title = "Should the customer and system data servers turn lazy too once the lazy setting goes away for good?"
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": f"Waits on [{title}](#/queue/question/1794382453.859209)", "detail": "", "url": None, "feature": "o1", "visuals": [], "prs": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
done({ wide: document.documentElement.scrollWidth, room: innerWidth });
""", board(history=[said]), "390,844")
    assert seen["wide"] <= seen["room"]


def test_choosing_a_lane_keeps_the_lane_grid_where_he_scrolled_it_and_esc_steps_back_one_level(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/work/Platform/SandSQL';
await wait(100);
const cards = document.querySelector('#main .lane-cards');
cards.scrollTop = 400;
const lane = [...cards.querySelectorAll('.lane-card')].find((card) => card.offsetTop > 400);
lane.click();
await wait(100);
const chosen = { hash: location.hash, same: document.querySelector('#main .lane-cards') === cards, top: cards.scrollTop, on: cards.querySelector('.lane-card.on') === lane };
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
await wait(100);
done({ chosen, up: location.hash });
""", board())
    assert seen["chosen"]["hash"].startswith("#/work/Platform/SandSQL/j")
    assert {key: value for key, value in seen["chosen"].items() if key != "hash"} == {"same": True, "top": 400, "on": True}
    assert seen["up"] == "#/work/Platform/SandSQL"


def test_a_code_embed_wider_than_its_card_scrolls_inside_its_box_and_keeps_copy_and_enlarge_in_reach(tmp_path: Path) -> None:
    long_line = "ctx.restate.run(() => RunAssignment.placeWokenAgents(ctx, { teamId: ctx.restate.key, agents, window: 30, retries: 5 })),"
    seen = browse(tmp_path, f"""
location.hash = '#/history';
await wait(100);
const card = document.querySelector('#main .said-text');
card.style.width = '420px';
card.insertAdjacentHTML('beforeend', `<figure class="visual"><div class="visual-host drawn" data-kind="code" data-visual="code:ts" data-source="x"><div class="code-bar"><span>ts</span><span class="acts"><button type="button" class="linkish" data-copy>Copy</button><button type="button" class="linkish" data-enlarge>Enlarge</button></span></div><pre class="visual-code"><code>{long_line}</code></pre></div><button type="button" class="enlarge" data-enlarge aria-label="Enlarge"></button></figure>`);
await wait(50);
const figure = card.querySelector('figure.visual');
figure.scrollIntoView({{ block: 'center' }});
await wait(50);
const pre = figure.querySelector('pre');
const reach = (el) => {{ const r = el.getBoundingClientRect(); return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === el; }};
done({{ inside: pre.getBoundingClientRect().right <= card.getBoundingClientRect().right + 1, scrolls: pre.scrollWidth > pre.clientWidth, copy: reach(figure.querySelector('[data-copy]')), enlarge: reach(figure.querySelector('.code-bar [data-enlarge]')), floating: getComputedStyle(figure.querySelector(':scope > .enlarge')).display }});
""", board(history=[{"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "the placer", "detail": "", "url": None, "feature": "o1", "visuals": [], "prs": []}]))
    assert seen == {"inside": True, "scrolls": True, "copy": True, "enlarge": True, "floating": "none"}


def test_a_new_dashboard_version_reloads_the_page_unless_he_is_in_the_middle_of_something(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
let reloads = 0;
window.reloadPage = () => { reloads += 1; };
location.hash = '#/queue/all';
await wait(100);
document.querySelector('#main [data-ask]').click();
await wait(50);
await push({ ...MODEL, code: '/plugins/long-running/9.9.9/lib/drive', at: '2026-11-11T00:09:00-08:00' });
const typing = { reloads, chip: document.querySelector('#stale').textContent, shown: !document.querySelector('#stale').hidden, kept: document.querySelector('.qfoot').textContent.includes('12:00') };
document.activeElement.blur();
await push({ ...MODEL, code: '/plugins/long-running/9.9.9/lib/drive' });
done({ typing, idle: reloads });
""", board(queue=[QUESTION], code="/plugins/long-running/1.0.1/lib/drive"))
    assert seen == {"typing": {"reloads": 0, "chip": "The dashboard was updated. Reload", "shown": True, "kept": True}, "idle": 1}


def test_only_links_that_leave_the_dashboard_carry_the_leave_arrow(tmp_path: Path) -> None:
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "the flow", "detail": "", "url": None, "feature": "o1", "visuals": [], "prs": []}
    seen = browse(tmp_path, """
const marked = [];
for (const hash of ['#/queue/all', '#/now/Platform/SandSQL', '#/work', '#/history', '#/history/2026-11-10', '#/feature/o1']) {
  location.hash = hash;
  await wait(150);
  for (const el of document.querySelectorAll('#main a:not([target="_blank"]), #main button')) if (el.textContent.includes('↗')) marked.push(`${hash} ${el.textContent.trim().slice(0, 40)}`);
}
location.hash = '#/history/2026-11-10';
await wait(100);
done({ marked, lead: document.querySelector('#main .lead').textContent });
""", board(queue=[QUESTION], history=[said, *board()["history"]], status=[{"state": "running", "features": [FEATURE | {"cards": [{"title": "Raise the limit?", "url": QUESTION["answer"], "asked": "2026-11-10T23:00:00-08:00"}]}]}]))
    assert seen == {"marked": [], "lead": "Every moment of the day, newest first. Click a moment to follow it back to the work."}


def test_pausing_the_work_map_is_remembered_for_the_next_visit(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
localStorage.removeItem('motion');
location.hash = '#/work';
await wait(100);
document.querySelector('#main [data-motion]').click();
await wait(50);
done({ kept: localStorage.getItem('motion'), pressed: document.querySelector('#main [data-motion]').getAttribute('aria-pressed') });
""", board())
    assert seen == {"kept": "paused", "pressed": "true"}


def test_a_history_moment_with_a_long_link_wraps_on_a_phone_instead_of_widening_the_page(tmp_path: Path) -> None:
    live = {"at": "2026-11-10T23:59:30-08:00", "kind": "live", "text": "the fix is live", "detail": "https://buildkite.com/forge/release/builds/14449/steps/canvas/jobs/0192f3a1-7b5c-4c8e-9d2e-1f0a", "url": None, "feature": "o1"}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
done({ wide: document.documentElement.scrollWidth, room: innerWidth });
""", board(history=[live]), "390,844")
    assert seen["wide"] <= seen["room"]


VISITS = ["#/queue/all", "#/now", "#/now/Platform/SandSQL/o1", "#/feature/o1", "#/work", "#/work/Platform/SandSQL", "#/history", "#/history/2026-11-09"]
VISITED = f"""
const seen = [];
const fetched = window.fetch;
let screen = 'summary';
window.fetch = async (url, options) => {{
  const asked = [screen, String(url).replace(/\\d+/g, '0')];
  seen.push(asked);
  const reply = await fetched(url, options);
  asked.push((await reply.clone().text()).replace(/\\d+/g, '0').length);
  return reply;
}};
await fetch('/api/view');
for (screen of {json.dumps(VISITS)}) {{
  slices.clear();
  location.hash = screen;
  await wait(300);
}}
done(seen);
"""


def aged(n: int) -> dict:
    """The board of a drive whose open work stays put, one feature with two lanes, a decision for him and the 100 merges of its day, after n
    orders were done, merged, told to him and tapped earlier that day."""
    asked = {
        "question": "Raise the limit?", "options": ["Raise it", "Shard it"], "recommended": "Raise it", "asked": "2026-11-11T09:00:00-08:00",
        "url": "https://acme.slack.com/archives/D123/p1794416400000100", "card": {"title": "Raise the limit?", "body": "Why it matters", "options": ["Raise it", "Shard it"], "recommended": "Raise it"},
    }
    orders = {"o1": order("ship the fix", ["j1", "j2"], project="Platform", workstream="SandSQL")}
    jobs = {"j1": job("o1", "running", "drawing it"), "j2": job("o1", "waiting")}
    prs = [pr(number, "o1", state="merged", updated=f"2026-11-09T{8 + (30 + number) // 60:02d}:{(30 + number) % 60:02d}:00-08:00") for number in range(1, 101)]
    events = []
    for number in range(1000, 1000 + n):
        orders[f"o{number}"] = order(f"old order {number}", [], state="done", created="2026-11-09T01:00:00-08:00", updated="2026-11-09T02:00:00-08:00")
        prs.append(pr(number, f"o{number}", state="merged", updated="2026-11-09T03:00:00-08:00"))
        said = f"https://acme.slack.com/archives/D123/p{1794100000000000 + number}"
        events.append({"kind": "said", "at": "2026-11-09T04:00:00-08:00", "text": f"old order {number} is live", "url": said, "order": f"o{number}", "visuals": []})
        events.append({"kind": "tap", "at": "2026-11-09T04:30:00-08:00", "repo": "yasyf/cc-skills", "number": number, "label": "Approve"})
    shown = view.board(drive_of(orders, jobs, prs), found([asked]), "live", events, state.parse_time("2026-11-11T10:00:00-08:00"), set())
    return shown | {"code": "", "pid": 1, "links": [], "undo_seconds": 10, "repo": ""}


def test_each_screen_reads_the_same_requests_of_the_same_size_however_old_the_drive(tmp_path: Path) -> None:
    seen = {n: browse(tmp_path / str(n), VISITED, aged(n)) for n in (1, 100, 1000)}
    assert {screen for screen, _, _ in seen[1]} == {"summary", *VISITS}
    assert seen[1] == seen[100] == seen[1000]
