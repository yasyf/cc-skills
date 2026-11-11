import http.client
import json
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from drive import dashboard

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
    said: saidRow({ at: "2026-11-10T23:00:00Z", text: "", visuals: [{ kind, source, caption: "", picture }] }),
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


def test_the_web_draws_every_component_kind_natively_and_never_as_a_picture(tmp_path: Path) -> None:
    script = tmp_path / "web.mjs"
    script.write_text(WEB_RUNNER)
    app = Path(dashboard.__file__).parent / "site" / "app.js"
    drawn = subprocess.run(["node", str(script), str(app), WEB_RENDER], input=json.dumps(COMPONENTS), capture_output=True, text=True, check=True)
    for kind, surfaces in json.loads(drawn.stdout):
        for surface, shown in surfaces.items():
            assert "<img" not in shown, f"the web drew the {kind} on its {surface} surface as a picture: {shown}"
            assert f'data-visual="{kind}"' in shown, f"the web drew no native {kind} on its {surface} surface: {shown}"


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
