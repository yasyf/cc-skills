from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest
from lrdash import chat
from test_dashboard import dashboard

MODIFIED = datetime(2026, 10, 5, 5, 20, tzinfo=UTC)
INBOX = """\
GO root (10:04 PM PT) reco-bucket-2: lift the hold on #30388
HOLD (9:03 PM PT) reco-bucket-2: #30388 do NOT enqueue.
LANDED compact-continuity (10:20 PM PT) yasyf/cc-skills#227 merged
"""
STATE = {
    "generated_at": "2026-10-05T07:00:00Z",
    "drive": {"program": "release-v3", "drive": "900424b6", "repo": "Forge-AI/monorepo"},
    "errors": {},
    "views": [
        {"id": "owner", "section": "Needs the owner", "title": "Waiting on the owner", "type": "table", "data": {"rows": [{"cite": "ask:ask/000001", "at": "2026-10-05T06:00:00Z", "text": "pick a store"}], "total": 1}},
        {"id": "census", "section": "Census", "title": "Stacks at 0/0", "type": "stat", "data": {"value": 232, "of": 293, "delta": 2, "detail": {"head": "e52aa668b3"}, "at": "2026-10-05T06:53:00Z"}},
        {"id": "broken", "section": "Census", "title": "Broken", "type": "table", "error": "unknown source 'nope'"},
    ],
}


@pytest.fixture
def sources(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "deploy-go.md").write_text(INBOX)
    (tmp_path / "done-board").mkdir()
    (tmp_path / "done-board" / "census.md").write_text("line one\nline two\n")
    lines = [line.row() | {"cite": f"inbox:{line.file}:{line.number}"} for line in dashboard.parse_inbox("deploy-go.md", INBOX, [(3, MODIFIED)])]
    calls = []

    def ccn(*args: str) -> str:
        calls.append(args)
        if args[0] == "search":
            return json.dumps([{"kind": "note", "note": {"id": "4cb82750275819c8", "title": "store options", "updated_at": "2026-10-05T06:29:05Z"}}])
        return f"shown {args[1]}"

    rows = {
        "inbox": lines,
        "tasks": [{"id": "7", "status": "in_progress", "subject": "Owner item: approve the plan", "cite": "task:7"}],
        "prs": [{"pr": "30388", "lane": "reco-bucket-2", "title": "reco bucket", "cite": "pr:30388"}],
        "asks": [{"key": "ask/000001", "text": "pick a store", "cite": "ask:ask/000001"}],
        "owner": [{"kind": "ask", "title": "pick a store", "cite": "ask:ask/000001"}],
        "boards": [{"slug": "store-picks--1a2b", "status": "open", "title": "Store picks", "cite": "board:store-picks--1a2b"}],
        "platy": [{"stack": "infra/core-usw2-auto", "deployable": "no", "cite": "stack:infra/core-usw2-auto", "submitted": True}],
    }
    found = chat.Sources(state=STATE, state_dir=tmp_path, rows=lambda: rows, ccn=ccn)
    found.calls = calls
    return found


def test_search_needs_every_word_and_returns_newest_first(sources):
    refs = [hit["ref"] for hit in chat.search(sources, "#30388 reco-bucket-2")]
    assert refs == ["inbox:deploy-go.md:1", "inbox:deploy-go.md:2", "ccn:4cb82750"]


def test_search_reaches_every_source_once(sources):
    assert [hit["ref"] for hit in chat.search(sources, "owner item")][0] == "task:7"
    assert "pr:30388" in [hit["ref"] for hit in chat.search(sources, "reco bucket")]
    assert "board:store-picks--1a2b" in [hit["ref"] for hit in chat.search(sources, "store-picks")]
    assert [hit["ref"] for hit in chat.search(sources, "pick a store")].count("ask:ask/000001") == 1
    assert "stack:infra/core-usw2-auto" in [hit["ref"] for hit in chat.search(sources, "core-usw2-auto")]


def test_read_resolves_each_ref_kind(sources):
    assert "> " in chat.read(sources, "inbox:deploy-go.md:2").splitlines()[1]
    assert chat.read(sources, "file:done-board/census.md") == "line one\nline two\n"
    assert json.loads(chat.read(sources, "pr:30388"))["lane"] == "reco-bucket-2"
    assert json.loads(chat.read(sources, "ask:ask/000001"))["text"] == "pick a store"
    assert json.loads(chat.read(sources, "task:7"))["subject"].startswith("Owner item")
    assert json.loads(chat.read(sources, "board:store-picks--1a2b"))["status"] == "open"
    assert json.loads(chat.read(sources, "stack:infra/core-usw2-auto"))["deployable"] == "no"
    assert chat.read(sources, "ccn:4cb82750") == "shown 4cb82750"


@pytest.mark.parametrize("ref", ["file:../outside.md", "inbox:../done-board/census.md:1", "pr:1", "nonsense"])
def test_read_refuses_refs_outside_the_drive(sources, ref):
    with pytest.raises(LookupError):
        chat.read(sources, ref)


def test_digest_summarizes_every_view_by_ref():
    digest = chat.digest(STATE)
    assert "view:owner (Needs the owner / Waiting on the owner, 1 rows):" in digest
    assert "- ask:ask/000001 2026-10-05T06:00:00Z pick a store" in digest
    assert '232 of 293, delta 2, {"head": "e52aa668b3"}' in digest
    assert "error: unknown source 'nope'" in digest


def test_view_lists_views_and_rejects_unknown_ones():
    assert [item["id"] for item in json.loads(chat.view(STATE, None))] == ["owner", "census", "broken"]
    assert json.loads(chat.view(STATE, "census"))["data"]["value"] == 232
    with pytest.raises(LookupError):
        chat.view(STATE, "nope")


def test_the_page_config_carries_the_model_and_the_chat_token_never_the_key(monkeypatch):
    monkeypatch.setenv(chat.KEY_ENV, "secret")
    assert chat.site_config("t0k") == {"model": "gpt-oss-120b", "key": "t0k"}


def test_tailnet_host_reads_the_magicdns_name_only_while_tailscale_runs(monkeypatch):
    status = {"BackendState": "Running", "Self": {"DNSName": "studio.tail71af5d.ts.net."}}
    monkeypatch.setattr(dashboard, "run", lambda argv: json.dumps(status))
    assert dashboard.tailnet_host() == "studio.tail71af5d.ts.net"
    monkeypatch.setattr(dashboard, "run", lambda argv: json.dumps({"BackendState": "Stopped"}))
    assert dashboard.tailnet_host() is None
    assert dashboard.share(8993) is None


RUNNING = {"BackendState": "Running", "Self": {"DNSName": "studio.tail71af5d.ts.net."}}


def tailscale(forwards: dict, calls: list):
    def run(argv):
        calls.append(argv)
        return json.dumps(RUNNING if argv[1] == "status" else {"TCP": forwards} if argv[2] == "status" else "")

    return run


def test_share_forwards_the_dashboard_port_over_the_tailnet(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard, "run", tailscale({}, calls))
    assert dashboard.share(8993) == "http://studio.tail71af5d.ts.net:8993/"
    assert calls[-1] == ["tailscale", "serve", "--bg", "--yes", "--tcp", "8993", "tcp://127.0.0.1:8993"]


def test_share_never_replaces_another_services_forward(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard, "run", tailscale({"8993": {"TCPForward": "127.0.0.1:5432"}}, calls))
    assert dashboard.share(8993) is None
    assert not any("--bg" in argv for argv in calls)


def test_a_tailscale_failure_leaves_the_dashboard_local(monkeypatch):
    def run(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="failed to connect to local Tailscale daemon")

    monkeypatch.setattr(dashboard, "run", run)
    assert dashboard.share(8993) is None


def test_loopback_links_move_to_the_tailnet_host_and_every_other_link_stays():
    row = {"url": "http://127.0.0.1:61118/p/platy-round-4", "platy_url": "http://localhost:7377/v1", "doing_url": "/inbox/root.md?line=3", "pr_url": "https://github.com/o/r/pull/1", "reason_url": None, "text": "http://127.0.0.1:1/"}
    assert dashboard.on_tailnet(row, "studio.tail71af5d.ts.net") == row | {"url": "http://studio.tail71af5d.ts.net:61118/p/platy-round-4", "platy_url": "http://studio.tail71af5d.ts.net:7377/v1"}


def test_the_owner_row_for_a_board_links_to_the_tailnet_url(tmp_path, monkeypatch):
    board = {"title": "Platy round 4: five picks", "slug": "platy-round-4", "status": "open", "at": "2026-10-06T09:00:00Z", "url": "http://127.0.0.1:61118/p/platy-round-4", "cite": "board:platy-round-4", "submitted": "not submitted", "answered": 0, "asks": 5, "closed": False}
    monkeypatch.setattr(dashboard.Collector, "boards", lambda self: [board])
    monkeypatch.setattr(dashboard.Collector, "cci", lambda self, path, timeout=None, **query: {})
    monkeypatch.setattr(dashboard, "run", lambda argv, cwd=None: json.dumps(RUNNING) if argv[0] == "tailscale" else json.dumps({"rows": []}) if "ledger" in argv else "")
    collector = dashboard.Collector({"drive": "d", "state_dir": str(tmp_path), "checkout": str(tmp_path), "ledger": "L", "repo": "o/r", "started_at": "2026-10-06T00:00:00Z", "sessions": []})
    _, sources = collector.snapshot()
    assert [row["url"] for row in sources["owner"]] == ["http://studio.tail71af5d.ts.net:61118/p/platy-round-4"]
    assert sources["boards"][0]["url"] == "http://studio.tail71af5d.ts.net:61118/p/platy-round-4"


@pytest.mark.parametrize(("host", "status"), [("127.0.0.1:{port}", 200), ("localhost:{port}", 200), ("studio.tail71af5d.ts.net:{port}", 200), ("rebound.example:{port}", 421)])
def test_the_server_answers_only_its_own_authorities(tmp_path, host, status):
    server = dashboard.bind("127.0.0.1", 0, dashboard.Collector({"drive": "d", "state_dir": str(tmp_path)}), 60)
    port = server.server_address[1]
    server.authorities.add(f"studio.tail71af5d.ts.net:{port}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/healthz", headers={"Host": host.format(port=port)})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == status
    except urllib.error.HTTPError as failure:
        assert failure.code == status
    finally:
        server.shutdown()


def test_the_dashboard_page_carries_the_chat_window_and_the_action_token():
    page = dashboard.page("tok3n").decode()
    assert page.count('id="lrchat"') == 1
    assert page.index('id="lrchat"') < page.index("</body>")
    assert '<meta name="dashboard-action" content="tok3n">' in page


def test_the_relay_carries_the_server_key_and_a_named_agent(monkeypatch):
    monkeypatch.setenv(chat.KEY_ENV, "secret")
    request = chat.relay(b"{}")
    assert request.full_url == chat.UPSTREAM
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.get_header("User-agent") == chat.USER_AGENT


def test_read_resolves_view_refs(sources):
    assert json.loads(chat.read(sources, "view:census"))["data"]["value"] == 232
