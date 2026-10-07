from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from livedash import chat, registry, server

ACTIONS = '''
from livedash import Col, Context, Table, component


def ping(ctx: Context, row: dict, text: str) -> str:
    return f"pinged {row['key']} with {text}"


@component("pings", "Pings", question="Does the test card answer its one question?", reads=["a test fixture"], every="manual", actions={"ping": ping})
def pings(ctx: Context) -> Table:
    return Table([Col("name", "Name")], [{"key": "r1", "cite": "ping:r1", "name": "first row", "at": "2026-10-06T10:00:00Z"}])
'''
LAYOUT = """title: Served
banner: Tailor this layout.
sections:
  - title: Activity
    components:
      - {use: local.pings, id: pings, pinned: true}
      - {use: local.pings, id: echo, title: Echo}
"""
RUNNING = {"BackendState": "Running", "Self": {"DNSName": "studio.tail0000.ts.net."}}


@pytest.fixture
def served(tmp_path):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "pings.py").write_text(ACTIONS)
    (tmp_path / "layout.yaml").write_text(LAYOUT)
    (tmp_path / "context.json").write_text(json.dumps({"id": "served", "title": "Served", "repo": "o/r"}))
    dashboard = server.bind("127.0.0.1", 0, tmp_path, "/script.py")
    dashboard.reload()
    dashboard.scheduler.run_once("pings")
    threading.Thread(target=dashboard.serve_forever, daemon=True).start()
    yield dashboard
    dashboard.shutdown()
    registry.forget(registry.LOCAL_PACKAGE)


def call(dashboard, path: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    port = dashboard.server_address[1]
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode(), response.headers
    except urllib.error.HTTPError as failure:
        return failure.code, failure.read().decode(), failure.headers


def test_each_dashboard_prefers_a_stable_port():
    assert (server.preferred_port("899e5f7a"), server.preferred_port("ddc78c14")) == (8936, 8732)


def test_state_carries_sections_cards_counts_and_the_banner(served):
    status, body, headers = call(served, "/state.json")
    state = json.loads(body)
    assert status == 200
    assert state["dashboard"] == {"id": "served", "title": "Served", "repo": "o/r", "program": None}
    assert state["banner"] == "Tailor this layout." and state["layout_error"] is None
    assert state["sections"] == [{"title": "Activity", "collapsed": False, "cards": ["pings", "echo"], "as_of": state["cards"][1]["as_of"]}]
    assert state["cards"][0] | {"as_of": None, "ms": None} == {"id": "pings", "use": "local.pings", "title": "Pings", "question": "Does the test card answer its one question?", "section": "Activity", "width": 1, "pinned": True, "fold": False, "phone": None, "every": "manual", "kind": "table", "payload": state["cards"][0]["payload"], "as_of": None, "status": "ok", "error": None, "ms": None, "actions": ["ping"]}
    assert state["counts"] == {"error": 0, "stale": 0, "hung": 0, "pending": 0}
    assert (state["cards"][0]["payload"]["rows"][0]["cite"], state["cards"][1]["payload"]["rows"], state["cards"][1]["payload"]["note"]) == ("ping:r1", [], "1 more under Pings")
    assert call(served, "/state.json", headers={"If-None-Match": headers["ETag"]})[0] == 304


def test_a_card_reads_as_markdown(served):
    assert call(served, "/cards/pings.md")[1] == "## Pings\n\n| Name | Cite |\n|---|---|\n| first row | ping:r1 |\n"
    assert call(served, "/cards/nope.json")[0] == 404


def test_a_broken_layout_keeps_the_last_good_one_and_names_the_line(served):
    (served.dir / "layout.yaml").write_text(LAYOUT.replace("pinned: true", "pinned: true, colour: red"))
    served.seen = ()
    served.reload()
    state = served.state()
    assert state["layout_error"].startswith("layout.yaml:6: card has unknown key colour")
    assert [card["id"] for card in state["cards"]] == ["pings", "echo"]


@pytest.mark.parametrize(("host", "status"), [("127.0.0.1:{port}", 200), ("localhost:{port}", 200), ("studio.tail0000.ts.net:{port}", 200), ("rebound.example:{port}", 421)])
def test_the_server_answers_only_its_own_authorities(served, host, status):
    port = served.server_address[1]
    served.authorities.add(f"studio.tail0000.ts.net:{port}")
    assert call(served, "/healthz", headers={"Host": host.format(port=port)})[0] == status


def test_health_names_the_drive_script_and_packs(served):
    assert json.loads(call(served, "/healthz")[1]) | {"pid": 0} == {"drive": "served", "script": "/script.py", "packs": {}, "pid": 0}


def test_actions_and_refresh_need_the_page_token_from_the_same_origin(served):
    token = {server.ACTION_HEADER: served.action_token}
    body = json.dumps({"key": "r1", "text": "hello   there"}).encode()
    assert call(served, "/act/pings/ping", "POST", {}, body)[0] == 403
    assert call(served, "/act/pings/ping", "POST", token | {"Origin": "http://evil.example"}, body)[0] == 403
    status, reply, _ = call(served, "/act/pings/ping", "POST", token, body)
    assert (status, json.loads(reply)) == (200, {"message": "pinged r1 with hello there"})
    assert call(served, "/act/pings/nope", "POST", token, body)[0] == 404
    assert call(served, "/act/pings/ping", "POST", token, json.dumps({"key": "r9"}).encode())[0] == 404
    assert call(served, "/refresh/pings", "POST", token, b"")[0] == 202
    assert call(served, "/refresh/nope", "POST", token, b"")[0] == 404


def test_shutdown_needs_the_server_token(served):
    assert call(served, "/shutdown", "POST", {server.TOKEN_HEADER: "wrong"}, b"")[0] == 403
    assert not served.stopping.is_set()


def test_the_card_command_reads_a_card_from_the_running_server(served, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("live_dashboard_cli", server.SKILL / "scripts" / "live-dashboard.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(server, "running", lambda directory: {"url": f"http://127.0.0.1:{served.server_address[1]}/"})
    assert cli.main(["card", "--dir", str(served.dir), "pings"]) == 0
    assert capsys.readouterr().out.endswith("| first row | ping:r1 |\n")
    with pytest.raises(SystemExit, match="no card nope"):
        cli.main(["card", "--dir", str(served.dir), "nope"])


def test_the_page_carries_the_chat_window_and_the_action_token():
    page = server.page("tok3n").decode()
    assert page.count('id="ldchat"') == 1
    assert page.index('id="ldchat"') < page.index("</body>")
    assert '<meta name="dashboard-action" content="tok3n">' in page


def test_a_server_from_another_script_or_pack_set_is_retired():
    record = {"alive": {"drive": "d", "script": "/new.py", "packs": {"lr": "/p"}}}
    assert server.current(record, "/new.py", {"lr": "/p"})
    assert not server.current(record, "/old.py", {"lr": "/p"})
    assert not server.current(record, "/new.py", {})
    assert not server.current({"alive": {"drive": "d", "script": "/new.py"}}, "/new.py", {"lr": "/p"})


def test_running_ignores_a_record_another_dashboard_answers(tmp_path, monkeypatch):
    (tmp_path / server.SERVER_FILE).write_text(json.dumps({"drive": "d", "url": "http://127.0.0.1:1/"}))
    monkeypatch.setattr(server, "health", lambda record: {"drive": "other"})
    assert server.running(tmp_path) is None
    monkeypatch.setattr(server, "health", lambda record: {"drive": "d"})
    assert server.running(tmp_path)["alive"] == {"drive": "d"}


def tailscale(forwards: dict, calls: list):
    def run(argv):
        calls.append(argv)
        return json.dumps(RUNNING if argv[1] == "status" else {"TCP": forwards} if argv[2] == "status" else "")

    return run


def test_share_forwards_the_port_over_the_tailnet(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "run", tailscale({}, calls))
    assert server.share(8936) == "http://studio.tail0000.ts.net:8936/"
    assert calls[-1] == ["tailscale", "serve", "--bg", "--yes", "--tcp", "8936", "tcp://127.0.0.1:8936"]


def test_share_never_replaces_another_services_forward(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "run", tailscale({"8936": {"TCPForward": "127.0.0.1:5432"}}, calls))
    assert server.share(8936) is None
    assert not any("--bg" in argv for argv in calls)


def test_a_stopped_or_failing_tailscale_leaves_the_dashboard_local(monkeypatch):
    monkeypatch.setattr(server, "run", lambda argv: json.dumps({"BackendState": "Stopped"}))
    assert server.tailnet_host() is None and server.share(8936) is None

    def fail(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="failed to connect to local Tailscale daemon")

    monkeypatch.setattr(server, "run", fail)
    assert server.share(8936) is None


def test_loopback_links_move_to_the_tailnet_host_and_every_other_link_stays():
    card = {"payload": {"rows": [{"url": "http://127.0.0.1:61118/p/board", "pr_url": "https://github.com/o/r/pull/1", "text": "http://127.0.0.1:1/", "link": "http://localhost:7377/v1"}]}}
    moved = server.on_tailnet(card, "studio.tail0000.ts.net")["payload"]["rows"][0]
    assert moved == {"url": "http://studio.tail0000.ts.net:61118/p/board", "pr_url": "https://github.com/o/r/pull/1", "text": "http://127.0.0.1:1/", "link": "http://studio.tail0000.ts.net:7377/v1"}


def test_chat_searches_reads_and_digests_the_cards(served, tmp_path):
    (tmp_path / "notes.md").write_text("line one\nline two\n")
    sources = chat.Sources(served.state(), tmp_path, lambda *args: json.dumps([{"kind": "note", "note": {"id": "4cb82750275819c8", "title": "first notes", "updated_at": "2026-10-06T09:00:00Z"}}]) if args[0] == "search" else f"shown {args[1]}")
    assert [hit["ref"] for hit in chat.search(sources, "first")] == ["ping:r1", "ccn:4cb82750"]
    assert json.loads(chat.read(sources, "ping:r1"))["card"] == "pings"
    assert chat.read(sources, "file:notes.md") == "line one\nline two\n"
    assert chat.read(sources, "ccn:4cb82750") == "shown 4cb82750"
    assert json.loads(chat.read(sources, "card:pings"))["kind"] == "table"
    assert [item["id"] for item in json.loads(chat.card(sources.state, None))] == ["pings", "echo"]
    digest = chat.digest(sources.state)
    assert "card:pings (Activity / Pings, table, ok, as of" in digest and "| first row |" in digest
    for ref in ("file:../outside.md", "inbox:../notes.md:1", "ping:r9", "nonsense"):
        with pytest.raises(LookupError):
            chat.read(sources, ref)


def test_the_chat_relay_carries_the_server_key_and_the_page_gets_only_a_token(monkeypatch):
    monkeypatch.setenv(chat.KEY_ENV, "server-side-value")
    request = chat.relay(b"{}")
    assert request.full_url == chat.UPSTREAM
    assert request.get_header("Authorization") == "Bearer server-side-value"
    assert chat.site_config("t0k") == {"model": chat.MODEL, "key": "t0k"}


@pytest.mark.parametrize("template", ["live-dashboard.html", "live-dashboard-chat.html"])
def test_every_page_script_parses(template, tmp_path):
    html = (server.SKILL / "templates" / template).read_text()
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts
    for index, script in enumerate(scripts):
        path = tmp_path / f"{index}.js"
        path.write_text(script)
        checked = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
