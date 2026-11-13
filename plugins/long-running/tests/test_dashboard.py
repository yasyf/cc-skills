import contextlib
import html
import http.client
import json
import math
import os
import plistlib
import re
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self

import pytest
from drive import cli, dashboard, launchd, picture, state, view
from conftest import Chrome
from test_view import drive_of, found, job, order, pr

NET = dashboard.Tailnet("box", "box.tail1.ts.net", ["100.64.0.1", "fd7a::1"], "owner@example.com", False)


def test_links_lead_with_https_on_the_tailnet_name_when_the_tailnet_issues_certificates_else_the_bare_label() -> None:
    assert dashboard.links_of(41330, NET._replace(https=True)) == ["https://box.tail1.ts.net:41330/", "http://127.0.0.1:41330/"]
    assert dashboard.links_of(41330, NET) == ["http://box:41330/", "http://127.0.0.1:41330/"]
    assert dashboard.links_of(41330, None) == ["http://127.0.0.1:41330/"]


def test_rebuild_lists_reports_only_when_jobs_directory_membership_changes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["orders"] = {"o1": order("ship the page", ["j1", "j2"], project="Drive", workstream="Dashboard")}
        data["jobs"] = {job_id: job("o1", "running") for job_id in ("j1", "j2")}
    folder = state.drive_dir(demo) / "jobs"
    folder.mkdir(exist_ok=True)
    report = folder / "j1.replies.jsonl"
    report.write_text("{}\n")
    (folder / "j2.jsonl").write_text("{}\n")
    listed_at = folder.stat().st_mtime_ns
    listings: list[str] = []
    glob = Path.glob

    def listed(path: Path, pattern: str, *, case_sensitive: bool | None = None, recurse_symlinks: bool = False) -> Iterator[Path]:
        if path == folder:
            listings.append(pattern)
        return glob(path, pattern, case_sensitive=case_sensitive, recurse_symlinks=recurse_symlinks)

    monkeypatch.setattr(Path, "glob", listed)
    monkeypatch.setattr(dashboard, "home_repo", lambda checkout: None)
    board = dashboard.Board(demo, [])
    board.rebuild()
    board.rebuild()
    assert listings == ["*.replies.jsonl"]
    assert board.reports == {"j1"}
    assert {entry["id"]: entry["files"] for entry in board.model["work"]} == {"j1": ["report"], "j2": []}

    report.write_text('{}\n{"updated": true}\n')
    assert folder.stat().st_mtime_ns == listed_at
    board.rebuild()
    assert listings == ["*.replies.jsonl"]
    assert board.reports == {"j1"}
    assert {entry["id"]: entry["files"] for entry in board.model["work"]} == {"j1": ["report"], "j2": []}

    (folder / "j2.replies.jsonl").write_text("{}\n")
    os.utime(folder, ns=(listed_at + 1_000_000_000, listed_at + 1_000_000_000))
    board.rebuild()
    assert listings == ["*.replies.jsonl"] * 2
    assert board.reports == {"j1", "j2"}
    assert {entry["id"]: entry["files"] for entry in board.model["work"]} == {"j1": ["report"], "j2": ["report"]}

    report.unlink()
    os.utime(folder, ns=(listed_at + 2_000_000_000, listed_at + 2_000_000_000))
    board.rebuild()
    assert listings == ["*.replies.jsonl"] * 3
    assert board.reports == {"j2"}
    assert {entry["id"]: entry["files"] for entry in board.model["work"]} == {"j1": [], "j2": ["report"]}


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
    server = dashboard.Server(socket.create_server((dashboard.LOOPBACK, 0)), BaseHTTPRequestHandler, types.SimpleNamespace(name="box.tail1.ts.net"))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
    connection.request("GET", "/api/view", headers={"Host": f"box:{port}"})
    reply = connection.getresponse()
    connection.close()
    server.shutdown()
    assert (reply.status, reply.getheader("Location")) == (301, f"https://box.tail1.ts.net:{port}/api/view")


def test_the_page_stays_live_across_one_dropped_stream_and_says_not_live_once_its_reconnect_fails(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
const banner = () => !document.querySelector('#stale').hidden && document.querySelector('#stale').textContent.startsWith('Not live');
while (document.querySelector('#fresh').textContent !== 'Live') await wait(20);
EventSource.CONNECTING = 0;
STREAM.readyState = 0;
STREAM.onerror();
const dropped = banner();
STREAM.onopen();
STREAM.onerror();
const droppedAgain = banner();
STREAM.onerror();
done({ dropped, droppedAgain, failed: banner() });
""", board())
    assert seen == {"dropped": False, "droppedAgain": False, "failed": True}


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


def test_a_dashboard_no_launch_agent_keeps_is_stopped_and_handed_to_one_that_runs_the_newest_install_and_starts_it_again_after_any_exit(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = dashboard.port_of(demo)
    links = dashboard.links_of(port, None)
    answers = iter([{"links": links, "pid": 4242, "code": "/elsewhere"}, None, {"links": links}, {"links": links}])
    killed: list[tuple[int, int]] = []
    ran: list[tuple[str, ...]] = []
    config = tmp_path / "config"
    newest = tmp_path / "cache" / "long-running" / "9.9.9"
    (newest / "bin").mkdir(parents=True)
    (newest / "bin" / "drive").write_text("import sys\nprint(*sys.argv[1:])\n")
    (newest / "bin" / "drive").chmod(0o755)
    (config / "plugins").mkdir(parents=True)
    installs = [{"installPath": str(tmp_path / "cache" / "long-running" / "9.9.8"), "lastUpdated": "2026-11-11T00:00:00Z"}, {"installPath": str(newest), "lastUpdated": "2026-11-12T00:00:00Z"}]
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"long-running@skills": installs}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("OPENAI_API_KEY", "a session secret")
    monkeypatch.setattr(dashboard, "serving", lambda name, port: next(answers))
    monkeypatch.setattr(launchd, "kept", lambda label: False)
    monkeypatch.setattr(dashboard.os, "kill", lambda pid, signum: killed.append((pid, signum)))
    monkeypatch.setattr(launchd, "launchctl", lambda *args: ran.append(args))
    monkeypatch.setattr(launchd, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    assert dashboard.start(demo, port) == links
    plist = tmp_path / "LaunchAgents" / f"com.yasyf.long-running.dashboard.{demo}.plist"
    agent = plistlib.loads(plist.read_bytes())
    assert killed == [(4242, dashboard.signal.SIGTERM)]
    assert ran == [("bootstrap", f"gui/{os.getuid()}", str(plist))]
    assert (agent["KeepAlive"], agent["RunAtLoad"], agent["ThrottleInterval"]) == (True, True, launchd.RESTART_SECONDS)
    assert agent["StandardOutPath"] == agent["StandardErrorPath"] == str(state.drive_dir(demo) / "dashboard.log")
    assert "OPENAI_API_KEY" not in agent["EnvironmentVariables"]
    started = subprocess.run(agent["ProgramArguments"], env=agent["EnvironmentVariables"], capture_output=True, text=True, check=True)
    assert started.stdout.split() == ["dashboard", "--drive", demo, "--port", str(port), "--serve"]


def test_a_kept_dashboard_serving_its_links_is_left_alone_and_one_that_is_not_is_started_again_at_once(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    port = dashboard.port_of(demo)
    links = dashboard.links_of(port, None)
    ran: list[tuple[str, ...]] = []
    monkeypatch.setattr(launchd, "launchctl", lambda *args: ran.append(args))
    monkeypatch.setattr(dashboard, "serving", lambda name, port: {"links": links})
    assert dashboard.start(demo, port) == links
    assert ran == []
    answers = iter([None, {"links": links}, {"links": links}])
    monkeypatch.setattr(dashboard, "serving", lambda name, port: next(answers))
    assert dashboard.start(demo, port) == links
    assert ran == [("kickstart", "-k", f"gui/{os.getuid()}/com.yasyf.long-running.dashboard.{demo}")]


def test_a_drive_saved_by_a_newer_install_hands_the_port_to_that_install_which_answers_throughout(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    older = state.install_lock(state.VERSION)
    done = threading.Event()
    answers: list[object] = []
    handed: list[list[str]] = []
    reopened: list[threading.Thread] = []

    class Handed(Exception):
        pass

    class Stopped(Exception):
        pass

    def changes(name: str) -> Iterator[None]:
        while not done.is_set():
            yield
            time.sleep(0.05)
        raise Stopped

    def reopen(argv: list[str]) -> None:
        with contextlib.suppress(Stopped):
            cli.main(argv)

    def execv(executable: str, argv: list[str]) -> None:
        handed.append(argv)
        monkeypatch.setattr(dashboard.install, "upgraded", lambda: None)
        monkeypatch.setattr(state, "VERSION", "9.9.9")
        inherited = [f"--listening={os.dup(int(arg.removeprefix('--listening=')))}" if arg.startswith("--listening=") else arg for arg in argv]
        reopened.append(threading.Thread(target=reopen, args=(inherited[3:],), daemon=True))
        reopened[0].start()
        raise Handed

    def ask() -> None:
        while not done.is_set():
            try:
                with urllib.request.urlopen(dashboard.url_of(port) + "api/view", timeout=10) as answer:
                    answers.append(json.load(answer)["drive"])
            except OSError as error:
                answers.append(error)

    def newer_install_saves() -> None:
        while dashboard.serving(demo, port) is None:
            time.sleep(0.05)
        threading.Thread(target=ask, daemon=True).start()
        time.sleep(0.3)
        with state.flocked(older):
            state.store(demo, state.stored(demo) | {"saved_by": "9.9.9"})

    monkeypatch.setattr(dashboard, "changes", changes)
    monkeypatch.setattr(dashboard, "tailnet", lambda: None)
    monkeypatch.setattr(dashboard.Board, "hear", lambda self: None)
    monkeypatch.setattr(dashboard, "home_repo", lambda checkout: None)
    monkeypatch.setattr(dashboard.signal, "signal", lambda signum, handler: None)
    monkeypatch.setattr(dashboard.install, "upgraded", lambda: newer if state.saved_by(demo) == "9.9.9" else None)
    monkeypatch.setattr(dashboard.os, "execv", execv)
    with socket.create_server((dashboard.LOOPBACK, 0)) as probe:
        port = probe.getsockname()[1]
    threading.Thread(target=newer_install_saves, daemon=True).start()
    with pytest.raises(Handed):
        dashboard.serve(demo, port, [])
    handed_at = len(answers)
    deadline = time.monotonic() + 10
    while len(answers) < handed_at + 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    done.set()
    reopened[0].join(5)
    [argv] = handed
    assert argv[:-1] == [sys.executable, "-I", str(newer), "dashboard", "--drive", demo, "--port", str(port), "--serve"]
    assert re.fullmatch(r"--listening=\d+", argv[-1])
    assert len(answers) >= handed_at + 3
    assert set(answers) == {demo}


def test_an_answer_reaches_cc_slack_only_from_the_page_itself_for_a_card_still_waiting_on_that_option_and_leaves_history_to_its_card_command(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state.create(state.new_drive("demo", "coordinator", "s1", "/work", "D1"))
    card, question = "https://acme.slack.com/archives/D1/p100", "https://acme.slack.com/archives/D1/p200"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Approve", "Hold"], question: ["Raise it", "Shard it"]}
    asked = []
    monkeypatch.setattr(dashboard, "answer_card", lambda card, label, via: asked.append((card, label, via)) or {"answered": True, "label": label})
    monkeypatch.setattr(dashboard.view, "refresh_cards", lambda name: None)
    server, port = serving(board)
    elsewhere = post_answer(port, "https://evil.example", card, "Approve")
    gone = post_answer(port, f"http://box:{port}", card, "Merge")
    taken = post_answer(port, f"http://box:{port}", card, "Approve")
    picked = post_answer(port, f"http://box:{port}", question, "Shard it")
    server.shutdown()
    assert elsewhere[0] == 403
    assert gone == (409, {"error": dashboard.GONE})
    assert (taken, picked) == ((200, {"answered": True, "label": "Approve"}), (200, {"answered": True, "label": "Shard it"}))
    assert asked == [(card, "Approve", f"http://box:{port}/"), (question, "Shard it", f"http://box:{port}/")]
    assert list(state.read_events("demo")) == []


def test_an_answer_reaches_cc_slack_at_once_and_its_undo_reopens_its_card_through_cc_slack(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state.create(state.new_drive("demo", "coordinator", "s1", "/work", "D1"))
    card = "https://acme.slack.com/archives/D1/p100"
    board = dashboard.Board("demo", [])
    board.answers = {card: ["Approve", "Hold"]}
    asked, reopened = [], []
    monkeypatch.setattr(dashboard, "answer_card", lambda card, label, via: asked.append((card, label)) or {"answered": True, "label": label, "undo_until": "2026-11-11T00:10:00Z"})
    monkeypatch.setattr(dashboard, "cc_slack", lambda *args: reopened.append(args) or {})
    monkeypatch.setattr(dashboard.view, "refresh_cards", lambda name: None)
    server, port = serving(board)
    page = f"http://box:{port}"
    began = time.monotonic()
    sent = post_answer(port, page, card, "Approve")
    took = time.monotonic() - began
    undone = post(port, page, "/api/undo", {"card": card})
    server.shutdown()
    assert took < 1
    assert sent == (200, {"answered": True, "label": "Approve", "undo_until": "2026-11-11T00:10:00Z"})
    assert undone == (200, {"undone": True, "restored": True})
    assert asked == [(card, "Approve")]
    assert reopened == [("card-reopen", "--card", card)]
    assert list(state.read_events("demo")) == []


def test_a_tap_on_a_pr_is_kept_at_once_through_the_one_tap_recorder_unless_its_head_moved(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state.create(state.new_drive("demo", "coordinator", "s1", "/work", "D1"))
    key = "yasyf/cc-skills#7"
    board = dashboard.Board("demo", [])
    board.heads = {key: "abc1234"}
    tapped = []
    monkeypatch.setattr(dashboard.card, "answered", lambda ask: tapped.append(ask) or {"ok": True})
    server, port = serving(board)
    page = f"http://box:{port}"
    moved = post(port, page, "/api/tap", {"pr": key, "label": "Approve", "head": "0ld0000"})
    stranger = post(port, page, "/api/tap", {"pr": "yasyf/cc-skills#8", "label": "Approve", "head": "abc1234"})
    elsewhere = post(port, "https://evil.example", "/api/tap", {"pr": key, "label": "Approve", "head": "abc1234"})
    began = time.monotonic()
    sent = post(port, page, "/api/tap", {"pr": key, "label": "Hold", "head": "abc1234"})
    took = time.monotonic() - began
    server.shutdown()
    assert moved == (409, {"error": dashboard.MOVED})
    assert (stranger[0], elsewhere[0]) == (409, 403)
    assert (sent[0], sent[1]["label"], took < 1) == (200, "Hold", True)
    assert tapped == [{"repo": "yasyf/cc-skills", "number": 7, "label": "Hold", "heads": {key: "abc1234"}, "source": "dashboard", "action_ts": None, "waits": ""}]
    assert list(state.read_events("demo")) == []


def test_an_undo_cc_slack_refuses_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D1/p100"

    def refused(*args: str) -> dict:
        raise dashboard.DriveError(f"card {card} shows no answer to take back")

    monkeypatch.setattr(dashboard, "cc_slack", refused)
    server, port = serving(dashboard.Board("demo", []))
    late = post(port, f"http://box:{port}", "/api/undo", {"card": card})
    server.shutdown()
    assert late == (409, {"error": f"card {card} shows no answer to take back"})


WEB_RUNNER = """
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { dirname, join } from "node:path";
const quiet = { addEventListener() {} };
const context = vm.createContext({ document: quiet, window: quiet, URL, components: JSON.parse(readFileSync(0, "utf8")) });
const kit = readFileSync(join(dirname(process.argv[2]), "kit", "decisions.js"), "utf8");
console.log(vm.runInContext(kit + "\\n" + readFileSync(process.argv[2], "utf8") + "\\n" + process.argv[3], context));
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


def test_table_cells_preserve_slack_link_labels_code_pipes_and_escaped_pipes(tmp_path: Path) -> None:
    line = r"| PR | <https://github.com/acme/app/pull/41|Use a separate process> | `left|right` | left\|right |"
    cells = web(tmp_path, f"JSON.stringify(decisionCells({json.dumps(line)}))")
    assert cells == ["PR", "<https://github.com/acme/app/pull/41|Use a separate process>", "`left|right`", "left|right"]
    shown = web(tmp_path, f"model = {{ repo: '', review: [] }}; JSON.stringify(prose({json.dumps('[table block]' + chr(10) + '| PR | Change |' + chr(10) + '| --- | --- |' + chr(10) + '| <https://github.com/acme/app/pull/41|Use a separate process> | Isolate memory |')}))")
    assert '[table block]' not in shown
    assert shown.count('<th>') == 2
    assert shown.count('<td>') == 2
    assert '<a href="https://github.com/acme/app/pull/41" target="_blank" rel="noopener">Use a separate process</a>' in shown


COMPARED = "\n".join(
    [
        "A run's memory decides which scripts can stop their neighbours.",
        "*Show me*",
        "| Path | Pods lost a day | Code changed |",
        "| --- | --- | --- |",
        "| Isolate it | 0 | 2 files |",
        "| Keep it shared | 8 | none |",
    ]
)


@pytest.mark.parametrize(
    ("options", "body", "format"),
    [
        (["Isolate the script: add a process", "Keep shared memory: no new work"], COMPARED, "lab"),
        (["Isolate the script: add a process", "Keep shared memory: no new work"], "A run's memory decides which scripts can stop their neighbours.", "cockpit"),
        (["Isolate it", "Keep it shared", "Cap it", "Retire it"], COMPARED, "interview"),
    ],
)
def test_a_question_opens_in_the_format_its_card_carries(tmp_path: Path, options: list[str], body: str, format: str) -> None:
    card = {"title": "*Where* may a large script fail?", "options": options, "body": body}
    shown = web(tmp_path, f"JSON.stringify(decisionStory({json.dumps(card)}))")
    assert (shown["format"], shown["headline"], shown["context"]) == (format, "Where may a large script fail?", "A run's memory decides which scripts can stop their neighbours.")
    assert [option["label"] for option in shown["options"]] == options


def test_a_lab_reads_each_options_measured_values_from_its_cards_comparison_table(tmp_path: Path) -> None:
    card = {"title": "Where may a large script fail?", "options": ["Isolate the script: add a process", "Keep shared memory: no new work"], "body": COMPARED}
    shown = web(tmp_path, f"JSON.stringify(decisionStory({json.dumps(card)}).options)")
    assert shown == [
        {"label": card["options"][0], "title": "Isolate the script", "consequence": "Add a process", "metrics": [{"label": "Pods lost a day", "value": "0"}, {"label": "Code changed", "value": "2 files"}]},
        {"label": card["options"][1], "title": "Keep shared memory", "consequence": "No new work", "metrics": [{"label": "Pods lost a day", "value": "8"}, {"label": "Code changed", "value": "none"}]},
    ]


def test_every_address_in_a_line_shows_as_what_it_names_and_never_as_raw_text(tmp_path: Path) -> None:
    line = "READY on https://github.com/acme/app/pull/41, its card https://acme.slack.com/archives/D1/p100?thread_ts=1.2 and build https://buildkite.com/acme/test/builds/128084). See https://app.datadoghq.com/monitors/3 or https://example.com/x."
    shown = web(tmp_path, f"model = {{ repo: '', review: [] }}; JSON.stringify([inline({json.dumps(line)}), named({json.dumps(line)})])")
    assert shown == [
        'READY on <a href="https://github.com/acme/app/pull/41" target="_blank" rel="noopener">#41</a>, its card Slack message and build '
        '<a href="https://buildkite.com/acme/test/builds/128084" target="_blank" rel="noopener">test build 128084</a>). '
        'See <a href="https://app.datadoghq.com/monitors/3" target="_blank" rel="noopener">Datadog monitor</a> or <a href="https://example.com/x" target="_blank" rel="noopener">example.com</a>.',
        "READY on #41, its card Slack message and build test build 128084). See Datadog monitor or example.com.",
    ]


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
        '<h4 class="hd">Show me</h4><p>The DM as drafted</p>'
        '<blockquote class="quote">Hey Rowan! The grant throws <a href="https://sentry.io/1" target="_blank" rel="noopener">this error</a> at 9:29 PM.<br><br>Can you take it?</blockquote>'
        "<p>After the quote.</p>"
    )


def test_a_markdown_table_on_a_card_draws_as_a_table_wherever_it_sits_in_its_paragraph(tmp_path: Path) -> None:
    body = "\n".join(
        [
            "*Show me*",
            "Where each home keeps the pin",
            "| | Local table | Server table |",
            "| --- | --- | --- |",
            "| Written to | `_pinned_chats` | `chat_favorites` |",
            "| With no network | works | fails",
            "The server table needs the network.",
        ]
    )
    shown = web(tmp_path, f"model = {{ repo: '', review: [] }}; JSON.stringify(prose({json.dumps(body)}))")
    assert shown == (
        '<h4 class="hd">Show me</h4><p>Where each home keeps the pin</p>'
        '<div class="table-scroll-hint">Scroll to see every column ↔</div><div class="tablewrap" tabindex="0" role="region" aria-label="Comparison table; scroll for all columns"><table><thead><tr><th></th><th>Local table</th><th>Server table</th></tr></thead><tbody>'
        "<tr><td>Written to</td><td><code>_pinned_chats</code></td><td><code>chat_favorites</code></td></tr>"
        "<tr><td>With no network</td><td>works</td><td>fails</td></tr>"
        "</tbody></table></div>"
        "<p>The server table needs the network.</p>"
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


def test_the_screens_are_the_four_he_kept_and_pages(tmp_path: Path) -> None:
    assert web(tmp_path, "JSON.stringify(SCREENS.map(([name]) => name))") == ["queue", "now", "work", "history", "pages"]


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


def test_a_recording_is_served_by_the_byte_range_a_video_asks_so_it_seeks(tmp_path: Path) -> None:
    recording = tmp_path / "after.mov"
    recording.write_bytes(b"0123456789")
    board = dashboard.Board("demo", [])
    board.pictures = {str(recording)}
    server, port = serving(board)
    replies = []
    for asked in ({}, {"Range": "bytes=2-5"}, {"Range": "bytes=7-"}):
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", f"/api/picture?path={urllib.parse.quote(str(recording))}", headers={"Host": f"box:{port}"} | asked)
        reply = connection.getresponse()
        replies.append((reply.status, reply.getheader("Content-Type"), reply.getheader("Accept-Ranges"), reply.getheader("Content-Range"), reply.read()))
        connection.close()
    server.shutdown()
    assert replies == [
        (200, "video/quicktime", "bytes", None, b"0123456789"),
        (206, "video/quicktime", "bytes", "bytes 2-5/10", b"2345"),
        (206, "video/quicktime", "bytes", "bytes 7-9/10", b"789"),
    ]


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


def test_typed_words_reach_cc_slack_only_from_the_page_itself_and_only_under_a_card_thread_of_this_drive_which_keeps_those_under_a_card(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert [(event["card"], event["url"]) for event, _ in state.read_events(demo, 0) if event["kind"] == "asked"] == [(card, f"{card}#copy")]


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
    "created": "2026-11-10T20:00:00-08:00", "jobs": [], "parts": [], "where": None, "holdup": None, "heard": [],
}

RAIL = {"failed": [], "conflict": False, "draft": False, "branch": "lane", "base": "main", "stack": [], "landing": None, "approve_waits": None, "parts": [], "job": None, "job_said": "", "job_at": None}
QUESTION = {
    "priority": {"rank": 1, "incident": False, "overdue": False, "weight": 0}, "kind": "question", "tasks": [], "answer": "https://x.slack.com/archives/D1/p1794382453859209", "title": "Raise the limit?", "since": None, "holds": 0, "blocks": [], "thread": None,
    "card": {"title": "Raise the limit?", "body": "", "options": ["Raise it", "Shard it"], "recommended": "Raise it"},
}


def board(**changed: object) -> dict:
    lanes = [
        {"id": f"j{n}", "feature": "o1", "kind": "builder", "goal": f"lane {n}", "state": "running", "where": "local", "said": "", "updated": "2026-11-11T00:00:00-08:00", "prs": 0, "branch": None, "waits": 0, "files": [], "agent": f"a{n}"}
        for n in range(40)
    ]
    moments = [
        {"at": f"2026-11-10T{23 - n // 60:02d}:{59 - n % 60:02d}:00-08:00", "kind": "merged", "text": f"change {n}", "detail": "", "url": "https://github.com/a/b/pull/1", "feature": "o1"}
        for n in range(120)
    ]
    counts = {"queue": 0, "blocked": 0, "running": 1, "built": 0, "waiting": 0, "merged": 0, "live": 0, "not started": 0}
    return {
        "drive": "demo", "at": "2026-11-11T00:00:00-08:00", "counts": counts, "queue": [], "status": [{"state": "running", "features": [FEATURE]}], "cards": "live",
        "review": [], "work": lanes, "history": moments, "incidents": [], "bug_reports": [], "repo": "", "links": [], "channel": "D1", "pages": [],
    } | changed | {"queue": [item | {"priority": {"rank": rank, "incident": False, "overdue": False, "weight": item.get("holds", 0) + len(item.get("blocks", []))} | {key: value for key, value in item.get("priority", {}).items() if key != "rank"}} for rank, item in enumerate(changed.get("queue", []), 1)]}


def fresh(minutes: int) -> str:
    return state.stamp(datetime.now(UTC) - timedelta(minutes=minutes))


def browse(tmp_path: Path, steps: str, model: dict, size: str = "1200,600", *flags: str, before: str = "", drawn: bool = False) -> object:
    """Serve the dashboard of model from the real handler and load it in headless Chrome with only its stream stubbed, and its renderers unless
    drawn: the page reads the summary once, and push(model) shows the board model and hands the page its summary. Run before, a script ahead of
    app.js that may stand in for the server's reads, then steps, an async module that may call push(model), wait(ms) and until(test), and return
    what steps put in done(result); /test/embed serves EMBED, a stand-in for cc-review's embedded review. Each poll of until also waits on a
    fetch the server answers 50 ms later, since Chrome's virtual clock runs on while a renderer compiles off the page's thread, as viz's wasm
    does, but stops while a fetch is out, so a slow machine's draw is waited for and never spends the polls or the clock's budget."""
    harness = f"""<script>
const MODEL = {json.dumps(model)};
window.EventSource = class {{ constructor() {{ window.STREAM = this; fetch('/api/view').then((reply) => reply.text()).then((data) => this.onmessage({{ data }})); }} }};
const wait = (ms) => new Promise((done) => setTimeout(done, ms));
const until = async (test) => {{ for (let tries = 0; tries < 200 && !test(); tries++) await Promise.all([wait(50), fetch('/test/tick')]); }};
const push = async (model) => {{ const reply = await fetch('/test/push', {{ method: 'POST', body: JSON.stringify(model) }}); window.STREAM.onmessage({{ data: await reply.text() }}); await wait(50); }};
const done = (result) => {{ document.documentElement.dataset.result = JSON.stringify(result); }};
</script>"""
    site = tmp_path / "site"
    site.mkdir(parents=True)
    page = (SITE / "index.html").read_text().replace('<script src="/app.js"></script>', f'{harness}<script>{before}</script><script src="/app.js"></script><script type="module">await wait(100);\n{steps}</script>')
    (site / "index.html").write_text(page)
    for name in ("app.js", "app.css", "kit"):
        (site / name).symlink_to(SITE / name)
    if drawn:
        for name in ("visuals.js", "vendor"):
            (site / name).symlink_to(SITE / name)
    else:
        (site / "visuals.js").write_text("export function draw() { throw new Error('nothing draws in this test'); }\n")
    shown = dashboard.Board("demo", [])
    shown.show(model)
    server, port = serving(shown)

    class Pushed(server.RequestHandlerClass):
        def do_GET(self) -> None:
            if self.path == "/test/tick":
                time.sleep(0.05)
                return self.reply(b"", "text/plain")
            if not self.path.startswith("/test/embed"):
                return super().do_GET()
            self.reply(EMBED.encode(), "text/html; charset=utf-8")

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
await wait(200);
const canvas = document.querySelector('#main .canvas');
canvas.querySelector('[data-cam="in"]').click();
await wait(100);
const aimed = JSON.stringify(nowCamera.at);
await push({ ...MODEL, at: '2026-11-11T00:05:00-08:00', work: MODEL.work.map((lane) => ({ ...lane, goal: 'moved on' })) });
await wait(200);
done({ same: document.querySelector('#main .canvas') === canvas, aimed: JSON.stringify(nowCamera.at) === aimed, goal: canvas.querySelector('.topo-node.lane b').textContent });
""", board(), "1200,600", "--force-prefers-reduced-motion")
    assert kept == {"same": True, "aimed": True, "goal": "moved on"}


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
toast('card', { label: 'Approve', title: 'ship it', state: 'sent', undoUntil: new Date(Date.now() + 600000).toISOString() });
await undo('card');
done({ drawer, toast: document.querySelector('#toasts .toast .meta').textContent });
""", board(review=[RAIL | {"key": "a/b#7", "title": "change 7", "url": "https://github.com/a/b/pull/7", "head": "abc", "card": None}]))
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


def test_a_recording_draws_as_a_player_that_enlarges_only_from_its_corner_button_and_keeps_its_player_through_a_live_update(tmp_path: Path) -> None:
    visuals = [{"kind": "image", "source": "", "caption": "the click", "picture": "/after.mov"}, {"kind": "image", "source": "", "caption": "the page", "picture": "/after.png"}]
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "recorded it", "detail": "", "url": None, "feature": "o1", "visuals": visuals, "prs": []}
    newer = {**said, "at": "2026-11-10T23:59:50-08:00", "text": "a later line", "visuals": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
const figure = document.querySelector('#main figure.recording');
const player = figure.querySelector('video');
const drawn = { src: player.getAttribute('src'), controls: player.controls, label: player.getAttribute('aria-label'), images: figure.querySelectorAll('img').length, button: !!player.closest('button') };
player.click();
await wait(50);
const box = document.querySelector('#lightbox');
const afterPlayerClick = box.open;
figure.querySelector('[data-enlarge]').click();
await wait(50);
const opened = { open: box.open, players: box.querySelectorAll('video').length, src: box.querySelector('.lb-stage video').getAttribute('src'), caption: box.querySelector('.lb-caption').textContent, count: box.querySelector('.lb-bar .meta').textContent, original: box.querySelector('.lb-bar a.ext').getAttribute('href') };
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
await wait(50);
const stepped = box.querySelector('.lb-stage img').getAttribute('src');
box.querySelector('[data-close]').click();
await wait(50);
await push({ ...MODEL, at: '2026-11-11T00:05:00-08:00', history: NEWER });
const said = () => [...document.querySelectorAll('#main li[data-kind="said"] .said-text')].map((text) => text.querySelector('p').textContent);
await until(() => said().length === 2);
done({ drawn, afterPlayerClick, opened, stepped, closed: !box.open, rows: said(), kept: document.querySelector('#main figure.recording') === figure && figure.querySelector('video') === player });
""".replace("NEWER", json.dumps([newer, said])), board(history=[said]))
    assert seen == {
        "drawn": {"src": "/api/picture?path=%2Fafter.mov", "controls": True, "label": "the click", "images": 0, "button": False},
        "afterPlayerClick": False,
        "opened": {"open": True, "players": 1, "src": "/api/picture?path=%2Fafter.mov", "caption": "the click", "count": "1 of 2", "original": "/api/picture?path=%2Fafter.mov"},
        "stepped": "/api/picture?path=%2Fafter.png",
        "closed": True,
        "rows": ["a later line", "recorded it"],
        "kept": True,
    }


def test_a_recording_that_cannot_load_keeps_its_tile_and_says_so_with_a_link_to_the_original(tmp_path: Path) -> None:
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "recorded it", "detail": "", "url": None, "feature": "o1", "visuals": [{"kind": "image", "source": "", "caption": "the click", "picture": str(tmp_path / "gone.mov")}], "prs": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await until(() => document.querySelector('#main figure.recording[data-failed]'));
const figure = document.querySelector('#main figure.recording');
const box = figure.getBoundingClientRect();
const said = figure.querySelector('.recording-error');
done({ said: said.firstChild.textContent, original: said.querySelector('a').getAttribute('href'), player: figure.querySelector('video').hidden, enlarge: getComputedStyle(figure.querySelector('.enlarge')).display, size: [Math.round(box.width), Math.round(box.height)] });
""", board(history=[said]))
    assert seen == {"said": "Recording unavailable.", "original": f"/api/picture?path={urllib.parse.quote(str(tmp_path / 'gone.mov'), safe='')}", "player": True, "enlarge": "none", "size": [220, 96]}


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


def test_a_picture_in_disclosed_evidence_opens_in_the_lightbox_and_returns_to_the_question(tmp_path: Path) -> None:
    body = "Why it matters.\n\n![the lookup](/lookup.png)"
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
const evidence = document.querySelector('[data-story-evidence]');
const before = evidence.open;
evidence.querySelector('summary').click();
evidence.querySelector('[data-shot]').focus();
document.activeElement.click();
await wait(50);
done({ before, evidence: evidence.open, lightbox: document.querySelector('#lightbox').open, hash: location.hash });
""", board(queue=[QUESTION | {"card": QUESTION["card"] | {"body": body}}]))
    assert seen == {"before": False, "evidence": True, "lightbox": True, "hash": "#/queue/all/1794382453.859209"}


def test_closing_evidence_keeps_the_question_and_the_selected_path(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
document.querySelector('[data-story-pick="1"]').click();
const evidence = document.querySelector('[data-story-evidence]');
evidence.querySelector('summary').click();
evidence.querySelector('summary').click();
done({ open: evidence.open, selected: document.querySelector('[data-story-pick][aria-pressed="true"]').dataset.storyPick, title: document.querySelector('#item-title').textContent });
""", board(queue=[QUESTION]))
    assert seen == {"open": False, "selected": "1", "title": "Raise the limit?"}


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
        "strip": ["3 incidents open, 1 stale", "1 decision for you"],
        "opened": True,
        "rows": [
            ["#/feature/o2", ["waits on your answer", "last move Nov 11 12:00 AM"]],
            ["#/feature/o3", ["1 job working", "1 open PR", "last move Nov 11 12:00 AM"]],
            ["#/feature/o1", ["nothing moving since Nov 10 9:00 PM"]],
        ],
        "closed": True,
        "hash": "#/feature/o2",
    }


def test_the_top_strip_counts_bug_reports_beside_incidents_until_each_reporter_is_told_and_lists_them_by_stage(tmp_path: Path) -> None:
    pr = {"title": "fix", "url": "https://github.com/a/b/pull/3", "state": "open", "checks": "pending", "review": "none", "tap": None}
    outage = FEATURE | {"id": "o4", "title": "SandSQL journal failing"}
    fixing = FEATURE | {"id": "o1", "title": "Runs page errors", "jobs": [{"id": "j1", "feature": "o1", "kind": "builder", "goal": "fix the runs page", "state": "running", "where": "local", "said": "", "updated": "2026-11-11T00:00:00-08:00", "prs": 1, "branch": None, "waits": 0, "files": [], "agent": "a1"}], "prs": [pr]}
    merged = FEATURE | {"id": "o2", "title": "GET /api/runs fails", "updated": "2026-11-10T21:00:00-08:00"}
    told = FEATURE | {"id": "o3", "title": "Questions stopped showing"}
    reports = [
        {"id": "o3", "title": told["title"], "created": "2026-11-10T19:00:00-08:00", "stage": "told"},
        {"id": "o2", "title": merged["title"], "created": "2026-11-10T20:00:00-08:00", "stage": "merged"},
        {"id": "o1", "title": fixing["title"], "created": "2026-11-10T21:00:00-08:00", "stage": "fixing"},
    ]
    seen = browse(tmp_path, """
const strip = () => [...document.querySelectorAll('#alerts > *')].map((each) => each.textContent);
const before = strip();
document.querySelector('#alerts [popovertarget="bug-reports"]').click();
await wait(50);
const list = document.querySelector('#bug-reports');
const opened = list.matches(':popover-open');
const head = [list.querySelector('.inc-head b').textContent, list.querySelector('.inc-head span').textContent];
const rows = [...list.querySelectorAll('li a')].map((row) => [row.getAttribute('href'), row.querySelector('.meta').textContent.replace(/\\s+/g, ' ').split(' · ')]);
list.querySelector('li a').click();
await wait(50);
const closed = !list.matches(':popover-open');
const hash = location.hash;
await push({ ...MODEL, bug_reports: MODEL.bug_reports.map((report) => (report.id === 'o2' ? { ...report, stage: 'told' } : report)) });
done({ before, opened, head, rows, closed, hash, after: strip() });
""", board(status=[{"state": "running", "features": [outage, fixing, merged, told]}], incidents=[{"id": "o4", "title": outage["title"], "created": "2026-11-10T20:00:00-08:00"}], bug_reports=reports))
    assert seen == {
        "before": ["1 incident open, 1 stale", "2 bug reports open, 1 stale"],
        "opened": True,
        "head": ["2 bug reports open", "1 being fixed · 1 merged · 1 told, each open until its reporter is told the fix is live"],
        "rows": [
            ["#/feature/o1", ["Being fixed", "reported Nov 10 9:00 PM", "1 job working", "1 open PR", "last move Nov 11 12:00 AM"]],
            ["#/feature/o2", ["Fix merged, waits on proof it is live", "reported Nov 10 8:00 PM", "nothing moving since Nov 10 9:00 PM"]],
            ["#/feature/o3", ["Its reporter was told it is fixed", "reported Nov 10 7:00 PM"]],
        ],
        "closed": True,
        "hash": "#/feature/o1",
        "after": ["1 incident open, 1 stale", "1 bug report open"],
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
const look = () => ({ hash: location.hash, level: canvas.dataset.level, same: document.querySelector('#main .canvas') === canvas, top: scrollY, shown: getComputedStyle(world).visibility === 'visible' && Number(world.style.getPropertyValue('--k')) === nowCamera.at.k, scale: canvas.querySelector('.region[data-cam-key="Platform"]').getBoundingClientRect().height });
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
    assert {name: {key: value for key, value in view.items() if key not in ("scale", "top")} for name, view in seen.items()} == {
        "overview": {"hash": "#/now", "level": "all", "same": True, "shown": True},
        "project": {"hash": "#/now/Platform", "level": "project", "same": True, "shown": True},
        "stream": {"hash": "#/now/Platform/SandSQL", "level": "stream", "same": True, "shown": True},
        "task": {"hash": "#/now/Platform/SandSQL/o1", "level": "stream", "same": True, "shown": True, "sheet": "ship the fix"},
        "closed": {"hash": "#/now/Platform/SandSQL", "level": "stream", "same": True, "shown": True, "sheet": False},
    }
    assert len({view["top"] for view in seen.values()}) == 1
    assert seen["overview"]["scale"] < seen["project"]["scale"] < seen["stream"]["scale"]


def test_now_fills_the_window_and_a_plain_wheel_zooms_into_the_project_under_it_lists_what_waits_on_him_there_and_steps_back_out(tmp_path: Path) -> None:
    iris = FEATURE | {"id": "o2", "title": "chats open on the Mac", "project": "Iris", "workstream": "Desktop app"}
    asked = QUESTION | {"tasks": ["o1"], "project": "Platform", "workstream": "SandSQL", "rank_why": "holds 1 task"}
    seen = browse(tmp_path, """
location.hash = '#/now';
await wait(300);
const canvas = document.querySelector('#main .canvas');
const viewport = canvas.querySelector('.viewport');
const box = canvas.getBoundingClientRect();
const room = document.querySelector('#main').getBoundingClientRect();
const fills = { right: innerWidth - box.right <= 24, bottom: innerHeight - box.bottom <= 24, share: box.width * box.height / (room.width * room.height) > 0.6 };
const wheel = (deltaY, [x, y]) => viewport.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY, clientX: x, clientY: y }));
const middle = (el) => { const r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
const over = middle(canvas.querySelector('.region[data-cam-key="Platform"]'));
for (let i = 0; i < 10 && location.hash === '#/now'; i++) wheel(-120, over);
await wait(800);
const zoomedIn = location.hash;
const side = canvas.querySelector('.now-side');
const here = { narrow: side.getBoundingClientRect().width < 300, listed: [...side.querySelectorAll('.qrow-title')].map((title) => title.textContent), acts: [...side.querySelectorAll('.qrow-acts button')].map((button) => button.textContent), filtered: side.querySelector('.scope-work a.more').getAttribute('href') };
for (let i = 0; i < 12 && location.hash !== '#/now'; i++) wheel(120, middle(viewport));
await wait(800);
done({ fills, zoomedIn, here, zoomedOut: location.hash, same: document.querySelector('#main .canvas') === canvas });
""", board(queue=[asked], status=[{"state": "running", "features": [FEATURE, iris]}]), "1440,900", "--force-prefers-reduced-motion")
    assert seen == {
        "fills": {"right": True, "bottom": True, "share": True},
        "zoomedIn": "#/now/Platform",
        "here": {"narrow": True, "listed": ["Raise the limit?"], "acts": [], "filtered": "#/queue/all?project=Platform"},
        "zoomedOut": "#/now",
        "same": True,
    }


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_now_draws_each_project_with_open_work_as_a_readable_card_of_one_size_with_a_tip_per_segment_and_leaves_one_with_nothing_open_off(tmp_path: Path, size: str) -> None:
    card = {"title": "Raise the limit?", "url": QUESTION["answer"], "asked": "2026-11-10T23:00:00-08:00"}
    features = [
        FEATURE,
        FEATURE | {"id": "o2", "title": "journal replays", "state": "blocked", "cards": [card]},
        FEATURE | {"id": "o3", "title": "chats open on the Mac", "project": "Iris", "workstream": "Desktop app", "state": "waiting"},
        FEATURE | {"id": "o4", "title": "old guide", "project": "Docs", "workstream": "Guides", "state": "live"},
        *(FEATURE | {"id": f"o{n}", "title": f"shipped {n}", "workstream": "Tables", "state": "live"} for n in range(10, 40)),
    ]
    seen = browse(tmp_path, """
location.hash = '#/now';
await wait(800);
const canvas = document.querySelector('#main .canvas');
const cards = [...canvas.querySelectorAll('.region')].map((region) => {
  const box = region.getBoundingClientRect();
  const head = region.querySelector('.region-head');
  const name = head.querySelector('.region-name b');
  const open = head.querySelector('.region-open');
  const lines = [...head.children].map((line) => line.getBoundingClientRect());
  return {
    project: region.dataset.camKey,
    said: [name.textContent, head.querySelector('.region-name small').textContent, `${open.querySelector('b').textContent} ${open.querySelector('small').textContent}`, [...head.querySelectorAll('.need')].map((need) => need.textContent)],
    tips: [...head.querySelectorAll('.statebar i')].map((segment) => [segment.dataset.tip, segment.getBoundingClientRect().width >= 7.5]),
    size: `${Math.round(box.width)}x${Math.round(box.height)}`,
    readable: parseFloat(getComputedStyle(name).fontSize) * nowCamera.at.k > 15.9
      && [...head.querySelectorAll('.region-name small, .region-open small, .need')].every((small) => parseFloat(getComputedStyle(small).fontSize) * nowCamera.at.k > 10.9)
      && lines.every((line) => line.height > 0 && line.top >= box.top && line.bottom <= box.bottom && line.right <= box.right + 0.5),
    inside: box.left >= 0 && box.right <= innerWidth,
  };
});
canvas.querySelector('.region[data-cam-key="Platform"] .region-head .statebar i:last-child').dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await wait(50);
const tip = document.querySelector('#srctip');
const hovered = tip.matches(':popover-open') && tip.textContent;
canvas.querySelector('.region[data-cam-key="Platform"]').click();
await wait(800);
const segments = [...canvas.querySelectorAll('.region[data-cam-key="Platform"] .region-head .statebar i, .region[data-cam-key="Platform"] .stream-head .statebar i')];
done({
  cards: cards.map(({ size, ...each }) => each),
  sizes: new Set(cards.map((each) => each.size)).size,
  hovered,
  zoomed: { hash: location.hash, level: canvas.dataset.level, same: document.querySelector('#main .canvas') === canvas },
  wide: segments.length > 3 && segments.every((segment) => segment.dataset.tip && segment.getBoundingClientRect().width >= 7.5),
});
""", board(status=[{"state": "running", "features": features}]), size, "--force-prefers-reduced-motion")
    assert seen == {
        "cards": [
            {"project": "Iris", "said": ["Iris", "1 workstream", "1 active", []], "tips": [["1 waiting", True]], "readable": True, "inside": True},
            {
                "project": "Platform",
                "said": ["Platform", "2 workstreams", "2 active", ["1 question for you", "1 blocked"]],
                "tips": [["1 blocked", True], ["1 being built", True], ["30 live", True]],
                "readable": True,
                "inside": True,
            },
        ],
        "sizes": 1,
        "hovered": "30 live",
        "zoomed": {"hash": "#/now/Platform", "level": "project", "same": True},
        "wide": True,
    }


def test_now_zoomed_out_shows_all_twelve_project_cards_on_one_screen_each_with_its_whole_name_readable(tmp_path: Path) -> None:
    projects = ["Cloud agents", "Drive", "Drive tooling", "Durable agents", "Incidents", "Iris", "Iris desktop", "Memory", "Platform", "Platform reliability", "SandSQL", "Search"]
    features = [
        FEATURE | {"id": f"o{n}", "title": f"task {n}", "project": projects[n // 2], "workstream": f"Workstream {n}", "state": "blocked" if n % 2 else "waiting"}
        for n in range(2 * len(projects))
    ]
    seen = browse(tmp_path, """
location.hash = '#/now';
await wait(800);
const view = document.querySelector('#main .canvas .viewport').getBoundingClientRect();
done([...document.querySelectorAll('#main .canvas .region')].map((region) => {
  const box = region.getBoundingClientRect();
  const name = region.querySelector('.region-name b');
  return [region.dataset.camKey, box.top >= view.top && box.bottom <= view.bottom && box.left >= view.left && box.right <= view.right, parseFloat(getComputedStyle(name).fontSize) * nowCamera.at.k > 15.9 && name.scrollWidth <= name.clientWidth];
}));
""", board(status=[{"state": "running", "features": features}]), "1600,1000", "--force-prefers-reduced-motion")
    assert sorted(seen) == [[name, True, True] for name in projects]


def test_one_crumb_in_the_top_bar_names_the_screen_project_workstream_task_or_day_he_is_on(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
const crumb = async (hash) => {
  location.hash = hash;
  await wait(200);
  return [...document.querySelectorAll('#crumb a, #crumb strong')].map((step) => (step.tagName === 'A' ? [step.textContent, step.getAttribute('href')] : [step.textContent]));
};
const walked = {};
for (const hash of ['#/', '#/now', '#/now/Platform/SandSQL', '#/feature/o1', '#/work/Platform/SandSQL/j1', '#/history/2026-11-10', '#/now/Platform/SandSQL/o1']) walked[hash] = await crumb(hash);
document.querySelector('#crumb a[href="#/now/Platform"]').click();
await wait(200);
done({ walked, clicked: location.hash, trails: document.querySelectorAll('.crumb, .crumbs').length });
""", board())
    task = [["demo", "#/"], ["Now", "#/now"], ["Platform", "#/now/Platform"], ["SandSQL", "#/now/Platform/SandSQL"], ["ship the fix"]]
    assert seen == {
        "walked": {
            "#/": [["demo", "#/"], ["Your queue"]],
            "#/now": [["demo", "#/"], ["Now"]],
            "#/now/Platform/SandSQL": [["demo", "#/"], ["Now", "#/now"], ["Platform", "#/now/Platform"], ["SandSQL"]],
            "#/feature/o1": task,
            "#/work/Platform/SandSQL/j1": [["demo", "#/"], ["Work", "#/work"], ["Platform", "#/work/Platform"], ["SandSQL", "#/work/Platform/SandSQL"], ["j1"]],
            "#/history/2026-11-10": [["demo", "#/"], ["History", "#/history"], ["Nov 10"]],
            "#/now/Platform/SandSQL/o1": task,
        },
        "clicked": "#/now/Platform",
        "trails": 1,
    }


def test_now_draws_waiting_and_movement_as_labeled_metrics_and_lists_global_priority(tmp_path: Path) -> None:
    newer = QUESTION | {"since": "2026-11-10T23:00:00-08:00"}
    older = QUESTION | {"answer": "https://x.slack.com/archives/D1/p1794382400000000", "title": "Shard it?", "since": "2026-11-10T21:30:00-08:00"}
    tap = QUESTION | {"kind": "tap", "answer": "https://x.slack.com/archives/D1/p1794382453859300", "since": "2026-11-10T22:15:00-08:00"}
    owned = FEATURE | {"cards": [{"title": newer["title"], "url": newer["answer"], "asked": newer["since"]}]}
    seen = browse(tmp_path, """
location.hash = '#/now';
await wait(300);
done({
  metrics: [...document.querySelectorAll('#main .metric')].map((metric) => metric.textContent.replace('›', '').trim()),
  ranked: [...document.querySelectorAll('#main .qrow')].map((row) => row.dataset.rank),
  strip: [...document.querySelectorAll('#alerts > *')].map((chip) => chip.textContent),
  needs: [...document.querySelectorAll('#main .region[data-cam-key="Platform"] .region-head .need')].map((need) => need.textContent),
});
""", board(queue=[newer, older, tap], status=[{"state": "running", "features": [owned]}]))
    assert seen["strip"] == ["2 decisions for you", "1 PR to tap"]
    assert seen["needs"] == ["1 question for you"]
    assert seen["metrics"] == ["Decisions2", "PRs to approve1", "Moving1", "Blocked0", "Live0"]
    assert seen["ranked"] == ["1", "2"]


def test_a_task_sheet_heads_with_what_it_waits_on_and_lists_where_it_is_what_ships_what_went_wrong_and_what_comes_next(tmp_path: Path) -> None:
    words = " ".join(["refunds page: show the processing fee we keep, in the same line as the amount, and say why;"] * 3)
    red = {"title": "Fix: 🧾 show the kept fee", "url": "https://github.com/demo-org/shop/pull/418", "state": "open", "checks": "fail", "review": "none", "tap": None}
    merged = red | {"title": "New: 🏷️ the fee line", "url": "https://github.com/demo-org/shop/pull/401", "state": "merged", "checks": "pass"}
    lane = board()["work"][0] | {"feature": "o2"}
    jobs = [
        lane | {"id": "j2", "kind": "reader", "goal": "research: refund fees", "state": "done", "said": "PUSHED https://github.com/demo-org/shop/pull/401 shows the fee and links the policy", "updated": "2026-11-10T22:00:00-08:00"},
        lane | {"id": "j3", "goal": "refund fee line", "state": "failed", "said": "BLOCKED buildkite/test is red on https://github.com/demo-org/shop/pull/418 since the total rounds down", "updated": "2026-11-10T23:15:00-08:00"},
        lane | {"id": "j4", "goal": "round like the ledger", "state": "running", "said": "rounding fixed; adding its test next", "updated": "2026-11-10T23:50:00-08:00"},
    ]
    jobs = [each | {"word": {"text": each["said"], "at": each["updated"]}} for each in jobs]
    parts = [
        {"name": "fee line", "whole": False, "delivered": True, "posted": False, "job": "j2", "kind": "reader", "state": "done", "at": "2026-11-10T22:00:00-08:00"},
        {"name": "say why", "whole": False, "delivered": False, "posted": False, "job": "j4", "kind": "builder", "state": "running", "at": "2026-11-10T23:50:00-08:00"},
        {"name": "receipt copy", "whole": False, "delivered": False, "posted": False, "job": None, "kind": None, "state": None, "at": None},
    ]
    entry = FEATURE | {
        "id": "o2", "title": "Refund page shows the fee", "text": words, "state": "blocked", "prs": [red, merged], "jobs": jobs, "parts": parts,
        "where": {"text": jobs[0]["said"], "job": "j2", "state": "done", "at": jobs[0]["updated"]},
        "holdup": {"text": "red checks on #418", "card": None, "pr": "demo-org/shop#418", "url": red["url"]},
    }
    seen = browse(tmp_path, """
location.hash = '#/now/Platform/SandSQL';
await wait(300);
const row = document.querySelector('#main .stream.here a.task-row[href$="/o2"]');
const listed = row.querySelector('.task-body b').textContent;
row.click();
await wait(400);
const sheet = document.querySelector('#main .task-sheet');
const text = (el) => el.textContent.replace(/\\s+/g, ' ').trim();
const section = (name) => [...sheet.querySelectorAll('h2.sec')].find((head) => head.firstChild.textContent.trim() === name);
const items = (name) => [...section(name).nextElementSibling.querySelectorAll('li')];
const report = (el) => [el.querySelector('.pill')?.textContent ?? null, text(el), [...el.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')])];
const where = [...sheet.querySelectorAll('dt')].find((dt) => dt.textContent === 'Where it is').nextElementSibling.querySelector('.report');
const kick = sheet.querySelector('.kick');
const eyebrows = [...sheet.querySelectorAll('.piece .eyebrow')];
const oneLine = (el) => { const range = document.createRange(); range.selectNodeContents(el); return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size === 1; };
eyebrows[0].dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await wait(50);
done({
  listed,
  title: sheet.querySelector('h2.sheet-title').textContent,
  words: sheet.querySelector('blockquote.said').textContent,
  head: [kick.querySelector('.pill').textContent, kick.querySelector('.holdup').textContent, kick.querySelector('.holdup [data-pr]')?.dataset.pr],
  where: report(where),
  ships: [section('What it ships').querySelector('.count').textContent, items('What it ships').map((li) => [...li.querySelectorAll('.line-text, small')].map(text))],
  wrong: items('What went wrong').map((li) => [li.querySelector('b').textContent, ...report(li.querySelector('.report'))]),
  next: items('Next').map((li) => [li.querySelector('b').textContent, ...report(li.querySelector('.report'))]),
  prs: [section('Pull requests').querySelector('.count').textContent, section('Pull requests').querySelector('.meta').textContent],
  eyebrows: eyebrows.map((eyebrow) => [eyebrow.textContent, eyebrow.dataset.tip, oneLine(eyebrow)]),
  tip: document.querySelector('#srctip').textContent,
});
""", board(status=[{"state": "blocked", "features": [entry]}, {"state": "running", "features": [FEATURE]}]), "1440,900")
    assert seen == {
        "listed": "Refund page shows the fee",
        "title": "Refund page shows the fee",
        "words": words,
        "head": ["Blocked", "red checks on #418", "demo-org/shop#418"],
        "where": ["Done", "Done since it last said PR up: #401 shows the fee and links the policy", [["#401", "https://github.com/demo-org/shop/pull/401"]]],
        "ships": [
            "1 of 3 delivered",
            [["fee line", "delivered · Nov 10 10:00 PM"], ["say why", "builder j4 running · Nov 10 11:50 PM"], ["receipt copy", "no job carries it yet"]],
        ],
        "wrong": [["builder j3", "Blocked", "Blocked buildkite/test is red on #418 since the total rounds down", [["#418", "https://github.com/demo-org/shop/pull/418"]]]],
        "next": [["builder j4", None, "rounding fixed; adding its test next", []]],
        "prs": ["2", "1 merged"],
        "eyebrows": [["Pull request #418", "demo-org/shop#418", True], ["Pull request #401", "demo-org/shop#401", True]],
        "tip": "demo-org/shop#418",
    }


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


def test_the_lab_compares_every_path_against_its_cards_measured_columns(tmp_path: Path) -> None:
    question = QUESTION | {"card": QUESTION["card"] | {"options": ["Raise it: one setting", "Shard it: 16 prefixes"], "recommended": "Raise it: one setting", "body": COMPARED.replace("Isolate it", "Raise it").replace("Keep it shared", "Shard it")}}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await until(() => document.querySelector('[data-story-compare]'));
const label = document.querySelector('.decision-story .type-label').textContent;
document.querySelector('[data-story-compare]').click();
const table = document.querySelector('.story-lab table');
done({ label, rows: table.querySelectorAll('tbody tr').length, headers: [...table.querySelectorAll('th')].map((cell) => cell.textContent), first: [...table.querySelectorAll('tbody tr:first-child td')].map((cell) => cell.textContent) });
""", board(queue=[question]))
    assert seen == {"label": "Tradeoff lab", "rows": 2, "headers": ["Path", "What changes", "Pods lost a day", "Code changed"], "first": ["Raise it", "One setting", "0", "2 files"]}


def test_a_selected_path_stays_selected_when_the_pointer_leaves_and_does_not_send(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
const sent = [];
const original = fetch;
window.fetch = (url, options) => url === '/api/answer' ? (sent.push(JSON.parse(options.body)), new Promise(() => {})) : original(url, options);
document.querySelector('[data-story-pick="1"]').click();
document.body.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await wait(50);
done({ selected: document.querySelector('[data-story-pick][aria-pressed="true"]').dataset.storyPick, outcome: document.querySelector('.story-outcome h3').textContent, sent });
""", board(queue=[QUESTION]))
    assert seen == {"selected": "1", "outcome": "Shard it", "sent": []}


def test_a_selected_choice_shows_its_description_and_keyboard_selection_stays_on_the_question(tmp_path: Path) -> None:
    hints = {"Raise it": "The bucket's per-prefix cap goes to 5,500 writes a second.", "Shard it": "Each team's journal writes under 16 prefixes."}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
const said = () => document.querySelector('.story-outcome').textContent;
const first = said();
const choice = document.querySelector('[data-story-pick="0"]');
choice.focus();
choice.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
await wait(50);
done({ first, after: said(), focused: document.activeElement.dataset.storyPick, title: document.querySelector('#item-title').textContent });
""", board(queue=[QUESTION | {"card": QUESTION["card"] | {"hints": hints}}]))
    assert hints["Raise it"] in seen["first"]
    assert hints["Shard it"] in seen["after"]
    assert (seen["focused"], seen["title"]) == ("1", "Raise the limit?")


def test_arrow_keys_on_a_pr_cards_tabs_switch_tabs_and_leave_the_queue_where_it_is(tmp_path: Path) -> None:
    tap = {"kind": "tap", "tasks": [], "url": "https://github.com/a/b/pull/7", "title": "fix the lock", "answer": None, "thread": None, "card": None, "since": None, "holds": 0, "blocks": []}
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


DEMO = "https://github.com/demo-org/demo-app/pull"
SHOW_ME = "### Before\n\n" + "\n\n".join(f"Held lock {n} never lets go." for n in range(30)) + "\n\n### After\n\n" + "\n\n".join(f"Held lock {n} lets go after 30 s." for n in range(30))
READ = {
    "title": "Fix: time out the lock", "url": f"{DEMO}/41", "body": f"## Show me\n{SHOW_ME}", "show_me": SHOW_ME, "additions": 12, "deletions": 3, "changedFiles": 2,
    "headRefName": "lock-timeout", "baseRefName": "main", "isDraft": False, "row": {},
}
STACK = {
    "kind": "tap", "tasks": [], "title": "Fix: the lock stack", "url": f"{DEMO}/40", "answer": "https://x.slack.com/archives/D1/p1794382453859300", "thread": None, "since": None,
    "detail": "", "due": None, "links": [],
    "card": {
        "title": "Fix: approve the lock stack?", "options": ["Approve", "Hold"], "recommended": "Approve",
        "prs": [
            {"url": f"{DEMO}/40", "title": "move the lock", "head": "a" * 40, "summary": "Moves the lock.", "lands": "", "blocks": "Merged"},
            {"url": f"{DEMO}/41", "title": "time out the lock", "head": "b" * 40, "summary": "A held lock lets go at `lib/lock.py:12`.", "hate_most": "The old queue still waits at `lib/queue.py:90`.", "lands": "you approve", "blocks": ""},
            {"url": f"{DEMO}/42", "title": "drop the old queue", "head": "c" * 40, "summary": "Drops the old queue.", "lands": "", "blocks": "CI failed; the agent is fixing it"},
        ],
    },
    "prs": [
        {"title": "Fix: move the lock", "url": f"{DEMO}/40", "state": "merged", "checks": "pass", "review": "approved", "tap": None},
        {"title": "Fix: time out the lock", "url": f"{DEMO}/41", "state": "open", "checks": "pass", "review": "approved", "tap": "wanted"},
        {"title": "Fix: drop the old queue", "url": f"{DEMO}/42", "state": "open", "checks": "fail", "review": "none", "tap": None},
    ],
}
OPEN = [
    RAIL | {"key": f"demo-org/demo-app#{number}", "title": title, "url": f"{DEMO}/{number}", "head": head * 40, "card": None, "answer": None, "thread": None, "since": None}
    for number, title, head in ((41, "Fix: time out the lock", "b"), (42, "Fix: drop the old queue", "c"))
]
STAND_IN = f"""
window.HEARD = [];
window.FAILS = [];
window.STARTS = 0;
{{
  const served = window.fetch;
  const reply = (body, status = 200) => new Response(JSON.stringify(body), {{ status }});
  window.fetch = async (url, options) => {{
    if (String(url).startsWith('/api/pr/')) return reply({json.dumps(READ)});
    if (!String(url).startsWith('/api/review/')) return served(url, options);
    STARTS += 1;
    await wait(200);
    const error = FAILS.shift();
    return error ? reply({{ error }}, 502) : reply({{ url: `${{location.origin}}/test/embed` }});
  }};
}}
"""
EMBED = """<!doctype html><body>the review<script>
parent.postMessage({ type: 'cc-review:ready', files: ['lib/lock.py', 'lib/queue.py'] }, '*');
addEventListener('message', (event) => {
  parent.HEARD.push({ ...event.data, embed: new URLSearchParams(location.search).get('embed') });
  if (event.data.type !== 'cc-review:say') return;
  const refused = event.data.line === 90 ? { error: `line 90 of ${event.data.path} is outside the diff's hunks` } : {};
  parent.postMessage({ type: 'cc-review:said', id: event.data.id, ...refused }, '*');
});
</script>"""


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_a_stack_card_opens_on_its_first_pr_still_waiting_with_landed_prs_collapsed_and_the_chosen_pr_in_the_address(tmp_path: Path, size: str) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(300);
const rows = () => [...document.querySelectorAll('#main ol.stack > li')].map((li) => [li.className, ...[...li.querySelectorAll('.name, .fact')].map((part) => part.textContent)]);
const chosen = () => ({ hash: location.hash, row: document.querySelector('#main .stack [aria-current="true"]').dataset.row, link: document.querySelector('#main .type-label a').textContent });
const opened = { rows: rows(), ...chosen() };
document.querySelector('#main .stack [data-row="2"]').click();
await wait(100);
const picked = chosen();
location.hash = '#/queue/all/1794382453.859300/41';
await wait(100);
const linked = chosen();
await push({ ...MODEL, queue: [{ ...MODEL.queue[0], prs: MODEL.queue[0].prs.map((pr, i) => (i === 1 ? { ...pr, state: 'merged' } : pr)) }] });
await wait(100);
done({ opened, picked, linked, merged: { kinds: rows().map(([kind]) => kind), ...chosen() } });
""", board(queue=[STACK], review=OPEN), size, before=STAND_IN)
    address = "#/queue/all/1794382453.859300"
    assert seen == {
        "opened": {
            "rows": [
                ["gone", "demo-org/demo-app#40 · move the lock", "Landed"],
                ["", "demo-org/demo-app#41 · time out the lock", "Tests pass", "Review approved", "Approve lands it"],
                ["", "demo-org/demo-app#42 · drop the old queue", "Tests fail", "No review yet", "CI failed; the agent is fixing it"],
            ],
            "hash": f"{address}/41", "row": "1", "link": "demo-org/demo-app#41",
        },
        "picked": {"hash": f"{address}/42", "row": "2", "link": "demo-org/demo-app#42"},
        "linked": {"hash": f"{address}/41", "row": "1", "link": "demo-org/demo-app#41"},
        "merged": {"kinds": ["gone", "gone", ""], "hash": f"{address}/42", "row": "2", "link": "demo-org/demo-app#42"},
    }


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_a_pr_cards_tabs_share_one_pane_height_and_its_action_bar_stays_on_screen_above_the_review(tmp_path: Path, size: str) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(600);
const card = document.querySelector('#main .pr-card');
const measured = [];
for (const tab of ['show', 'change', 'context']) {
  document.querySelector(`#main [data-prtab="${tab}"]`).click();
  await wait(100);
  const actions = document.querySelector('#main .pr-decision-actions');
  actions.scrollIntoView({ block: 'center' });
  const bar = actions.getBoundingClientRect();
  const hit = document.elementFromPoint(bar.left + 30, bar.top + bar.height / 2);
  const frame = document.querySelector('#frames iframe');
  measured.push({ tab, card: Math.round(card.getBoundingClientRect().height), bar: Math.round(bar.top), shown: bar.bottom <= innerHeight && Boolean(hit.closest('.pr-decision-actions')), frame: !frame.hidden });
}
done(measured);
""", board(queue=[STACK], review=OPEN), size, before=STAND_IN)
    assert len({each["card"] for each in seen}) == 1
    assert len({each["bar"] for each in seen}) == 1
    assert [(each["tab"], each["shown"], each["frame"]) for each in seen] == [("show", True, False), ("change", True, True), ("context", True, False)]


def test_the_change_tab_shows_progress_while_cc_review_starts_then_why_it_failed_with_retry_then_the_embedded_review(tmp_path: Path) -> None:
    reason = "GitHub did not answer cc-review in time (cc-review said: net/http: TLS handshake timeout)"
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(20);
document.querySelector('#main [data-prtab="change"]').click();
await wait(20);
const pane = () => document.querySelector('#main .pr-pane.change');
const starting = { steps: [...pane().querySelectorAll('.steps li')].map((li) => [li.className, li.textContent]), bar: Boolean(pane().querySelector('.progress')) };
await wait(400);
const failed = { said: pane().querySelector('.refused').textContent, retry: pane().querySelector('[data-retry]').textContent, frames: document.querySelectorAll('#frames iframe').length };
pane().querySelector('[data-retry]').click();
await wait(20);
const retried = pane().querySelector('.steps .on').textContent;
await wait(600);
const frame = document.querySelector('#frames iframe');
const [slot, shown] = [pane().querySelector('[data-slot]'), frame].map((el) => { const box = el.getBoundingClientRect(); return [box.left, box.top, box.width, box.height].map(Math.round); });
done({ starting, failed, retried, embed: new URL(frame.src).search, title: frame.title, waiting: Boolean(pane().querySelector('.review-wait')), visible: getComputedStyle(frame).visibility, fits: String(slot) === String(shown), starts: STARTS });
""", board(queue=[STACK], review=OPEN), "1440,900", before=f"{STAND_IN}FAILS.push({json.dumps(reason)});")
    assert seen == {
        "starting": {"steps": [["on", "Start cc-review for demo-org/demo-app#41 at bbbbbbb"], ["", "Load its diff"]], "bar": True},
        "failed": {"said": f"cc-review could not open demo-org/demo-app#41: {reason}", "retry": "Retry", "frames": 0},
        "retried": "Start cc-review for demo-org/demo-app#41 at bbbbbbb",
        "embed": "?embed=1",
        "title": "Code and review of demo-org/demo-app#41",
        "waiting": False,
        "visible": "visible",
        "fits": True,
        "starts": 2,
    }


def test_the_pr_rail_jumps_into_show_me_and_the_change_and_adds_a_question_or_line_comment_to_its_review(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(600);
const rail = document.querySelector('#main .pr-rail');
for (const details of rail.querySelectorAll('details')) details.open = true;
const texts = (selector) => [...rail.querySelectorAll(selector)].map((el) => el.textContent.trim());
const tab = () => document.querySelector('#main [data-prtab][aria-selected="true"]').dataset.prtab;
const drawn = { heads: texts('.eyebrow'), facts: texts('.rail-block:first-child .fact'), lands: texts('.rail-list li'), spots: texts('[data-spot]'), cites: texts('[data-point][data-line]'), files: texts('[data-point]:not([data-line])') };
rail.querySelectorAll('[data-spot]')[1].click();
await wait(800);
const pane = document.querySelector('#main .pr-pane.show');
const after = pane.querySelectorAll('h4.hd')[1].getBoundingClientRect().top - pane.getBoundingClientRect().top;
const jumped = { tab: tab(), scrolled: pane.scrollTop > 0, near: after >= 0 && after < 40 };
const add = async (words) => {
  const box = rail.querySelector('form[data-note] textarea');
  box.value = words;
  box.dispatchEvent(new Event('input', { bubbles: true }));
  rail.querySelector('form[data-note]').requestSubmit();
  await wait(200);
  return rail.querySelector('form[data-note] > :last-child').textContent.replace(/\\d{1,2}:\\d{2} [AP]M/, '<time>');
};
rail.querySelector('[data-point][data-line="12"]').click();
await wait(100);
const pointed = { tab: tab(), at: rail.querySelector('.point code').textContent };
const commented = await add('why 30 s?');
rail.querySelector('[data-point][data-line="90"]').click();
await wait(100);
const refused = await add('and this one?');
rail.querySelector('[data-unpoint]').click();
await wait(50);
const asked = await add('does the queue drain first?');
done({ drawn, jumped, pointed, commented, refused, asked, heard: HEARD.map(({ id, ...said }) => said) });
""", board(queue=[STACK], review=OPEN), "1440,900", before=STAND_IN)
    shown = {"pr": 41, "embed": "1"}
    assert seen == {
        "drawn": {
            "heads": ["Attached to this change bbbbbbb", "Question or line comment", "Tell the drive what to change"],
            "facts": ["Tests pass", "Review approved"],
            "lands": ["demo-org/demo-app#40 Landed", "demo-org/demo-app#41 lands once you approve", "demo-org/demo-app#42 does not land on Approve: CI failed; the agent is fixing it"],
            "spots": ["Before", "After"],
            "cites": ["lib/lock.py:12", "lib/queue.py:90"],
            "files": ["lib/lock.py", "lib/queue.py"],
        },
        "jumped": {"tab": "show", "scrolled": True, "near": True},
        "pointed": {"tab": "change", "at": "lib/lock.py:12"},
        "commented": "✓ Added <time> on lib/lock.py:12. The review's assistant answers it in The change.",
        "refused": "Not added: line 90 of lib/queue.py is outside the diff's hunks",
        "asked": "✓ Added <time>. The review's assistant answers it in The change.",
        "heard": [
            {"type": "cc-review:show", "path": "lib/lock.py", **shown},
            {"type": "cc-review:say", "text": "why 30 s?", "path": "lib/lock.py", "line": 12, **shown},
            {"type": "cc-review:show", "path": "lib/queue.py", **shown},
            {"type": "cc-review:say", "text": "and this one?", "path": "lib/queue.py", "line": 90, **shown},
            {"type": "cc-review:say", "text": "does the queue drain first?", **shown},
        ],
    }


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_a_pr_outside_the_queue_opens_in_the_drawer_as_the_queue_cards_pr_component_with_focus_in_it(tmp_path: Path, size: str) -> None:
    tapped = {"title": "Fix: retry the upload", "url": f"{DEMO}/43", "state": "open", "checks": "pass", "review": "approved", "tap": "Approve"}
    entry = RAIL | {"key": "demo-org/demo-app#43", "head": "d" * 40, "group": "decided", "detail": "green, approved", "since": None, "answer": None, "thread": None, "card": None} | tapped
    seen = browse(tmp_path, """
location.hash = '#/feature/o1';
await wait(100);
const opener = document.querySelector('#main [data-pr="demo-org/demo-app#43"]');
opener.focus();
opener.click();
await wait(600);
const drawer = document.querySelector('#drawer');
const card = drawer.querySelector('.pr-card');
const opened = {
  open: drawer.open, hash: location.hash, parts: [...card.children].map((part) => part.className), rail: Boolean(drawer.querySelector('.pr-rail')),
  title: card.querySelector('.card-title').textContent, counter: Boolean(card.querySelector('.counter')), actions: card.querySelector('.pr-decision-actions').textContent.trim() + card.querySelector('.pr-actions').textContent.trim(),
  focus: document.activeElement.matches('#drawer [data-prtab][aria-selected="true"]'),
};
drawer.querySelector('[data-prtab="change"]').click();
await wait(300);
const frame = drawer.querySelector('.review-slot iframe');
const changed = { embed: new URL(frame.src).search, visible: getComputedStyle(frame).visibility, overlays: document.querySelectorAll('#frames iframe').length, files: [...drawer.querySelectorAll('[data-point]')].map((file) => file.textContent) };
drawer.querySelector('[data-close]').click();
await wait(50);
done({ opened, changed, closed: !drawer.open, back: document.activeElement === opener });
""", board(review=[entry], status=[{"state": "running", "features": [FEATURE | {"prs": [tapped]}]}]), size, before=STAND_IN)
    assert seen == {
        "opened": {
            "open": True, "hash": "#/feature/o1", "parts": ["card-content", "pr-decision-actions", "pr-tabs", "pr-pane show", "pr-pane change", "pr-pane context", "pr-actions"], "rail": True,
            "title": "Time out the lock", "counter": False, "actions": "ApproveHoldYou tapped Approve; green, approved.", "focus": True,
        },
        "changed": {"embed": "?embed=1", "visible": "visible", "overlays": 0, "files": ["lib/lock.py", "lib/queue.py"]},
        "closed": True,
        "back": True,
    }


def test_a_decision_shows_its_ask_box_beside_the_card_without_a_click_as_a_pr_card_does(tmp_path: Path) -> None:
    tap = {"kind": "tap", "tasks": [], "url": "https://github.com/a/b/pull/7", "title": "fix the lock", "answer": None, "thread": None, "card": None, "since": None, "holds": 0, "blocks": []}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const decision = { box: !!document.querySelector('#main .context-rail form.say textarea'), button: !!document.querySelector('#main [data-ask], #main .ask-open') };
document.querySelector('#main [data-move="1"]').click();
await wait(100);
done({ decision, pr: !!document.querySelector('#main .context-rail form.say textarea') });
""", board(queue=[QUESTION, tap]))
    assert seen == {"decision": {"box": True, "button": False}, "pr": True}


def test_a_decisions_rail_lists_each_held_task_once_with_its_place_and_state_the_asking_job_and_his_earlier_words(tmp_path: Path) -> None:
    other = {"title": "Other one?", "url": "https://x.slack.com/archives/D1/p1794382999000100", "asked": "2026-11-10T22:00:00-08:00"}
    asked = {"title": "Raise the limit?", "url": QUESTION["answer"], "asked": "2026-11-10T23:00:00-08:00"}
    shipping = FEATURE | {"cards": [asked]}
    stuck = FEATURE | {"id": "o2", "title": "unblock the lane", "state": "blocked", "workstream": None, "cards": [asked, other]}
    asker = board()["work"][0] | {"goal": "read the limits", "state": "waiting", "files": ["brief", "report"]}
    rail = {
        "held": [{"kind": "pr", "feature": "o1", "title": "raise it", "url": "https://github.com/a/b/pull/7"}, {"kind": "job", "feature": "o2", "title": "shard it", "branch": "lane/two"}],
        "asker": asker,
        "answers": [{"words": "newer words", "at": "2026-11-10T22:30:00-08:00", "mark": "stands"}, {"words": "older words", "at": "2026-11-10T21:00:00-08:00", "mark": None}],
    }
    item = QUESTION | {"holds": 2, "blocks": [{"id": "o2", "title": "unblock the lane"}, {"id": "o1", "title": "ship the fix"}], "rail": rail}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
const rail = document.querySelector('#main .context-rail');
for (const details of rail.querySelectorAll('details')) details.open = true;
const rows = [...rail.querySelectorAll('.held')].map((row) => ({
  where: row.querySelector('.crumbs').textContent.replace(/\\s+/g, ''),
  title: row.querySelector('.held-title').textContent,
  href: row.querySelector('.held-title').getAttribute('href'),
  state: row.querySelector('.pill').textContent,
  finished: row.querySelector('small').textContent,
}));
const job = { goal: rail.querySelector('.jobs .title').textContent.trim(), files: [...rail.querySelectorAll('.jobs [data-file]')].map((button) => button.textContent) };
const words = [...rail.querySelectorAll('.said-list q')].map((said) => said.textContent);
rail.querySelectorAll('.held small')[1].dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await wait(50);
const hovered = document.querySelector('#srctip').textContent;
const badge = getComputedStyle(document.querySelector('#srctip'), '::after').content;
rail.querySelector('.held-title').focus();
await wait(50);
done({ rows, job, words, hovered, badge, focused: document.querySelector('#srctip').textContent });
""", board(queue=[item], status=[{"state": "running", "features": [shipping, stuck]}]))
    assert seen == {
        "rows": [
            {"where": "Platform/SandSQL", "title": "ship the fix", "href": "#/feature/o1", "state": "Being built", "finished": "a/b#7 green and approved"},
            {"where": "Platform/Unfiled", "title": "unblock the lane", "href": "#/feature/o2", "state": "Blocked", "finished": "built on lane/two"},
        ],
        "job": {"goal": "read the limits", "files": ["Brief", "Report"]},
        "words": ["newer words", "older words"],
        "hovered": "unblock the lane. Blocked. Finished and held: built on lane/two. Waits on this answer and on “Other one?”.",
        "badge": "none",
        "focused": "ship the fix. Being built. Finished and held: a/b#7 green and approved. Waits on this answer.",
    }


LONG_BODY = "\n\n".join(f"Reason {n}: the shard map keeps every journal on one node, so a restart replays all of them before the first read answers." for n in range(14))


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_the_sidebar_discloses_evidence_without_page_overflow_and_preserves_a_draft_on_live_updates(tmp_path: Path, size: str) -> None:
    item = QUESTION | {"card": QUESTION["card"] | {"body": LONG_BODY}}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
const details = document.querySelector('[data-story-evidence]');
details.querySelector('summary').click();
const input = document.querySelector('.decision-sidebar textarea');
input.value = 'What changes for the writer?';
input.dispatchEvent(new Event('input', { bubbles: true }));
await push(MODEL);
done({ open: document.querySelector('[data-story-evidence]').open, draft: document.querySelector('.decision-sidebar textarea').value, overflow: document.documentElement.scrollWidth > innerWidth, title: document.querySelector('#item-title').textContent });
""", board(queue=[item]), size)
    assert seen == {"open": True, "draft": "What changes for the writer?", "overflow": False, "title": "Raise the limit?"}


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_selecting_a_decision_always_explains_that_review_precedes_sending(tmp_path: Path, size: str) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
done({ guidance: document.querySelector('.story-action small').textContent, send: document.querySelectorAll('#main [data-card]').length, review: document.querySelector('[data-story-review]').textContent });
""", board(queue=[QUESTION]), size)
    assert seen == {"guidance": "Choose first. Review before sending.", "send": 0, "review": "Review this choice ↵"}


def test_evidence_folds_in_full_without_clipping_a_table_or_losing_the_selected_path(tmp_path: Path) -> None:
    body = f"One sentence.\n\n| Shard | Nodes |\n| --- | --- |\n| a | 3 |\n| b | 4 |\n\n{LONG_BODY}"
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
document.querySelector('[data-story-pick="1"]').click();
const evidence = document.querySelector('[data-story-evidence]');
const before = evidence.open;
evidence.querySelector('summary').click();
const opened = { open: evidence.open, rows: evidence.querySelectorAll('tr').length, headers: [...evidence.querySelectorAll('th')].map((cell) => cell.textContent) };
evidence.querySelector('summary').click();
done({ before, opened, closed: !evidence.open, selected: document.querySelector('[data-story-pick][aria-pressed="true"]').dataset.storyPick });
""", board(queue=[QUESTION | {"card": QUESTION["card"] | {"body": body}}]))
    assert seen == {"before": False, "opened": {"open": True, "rows": 3, "headers": ["Shard", "Nodes"]}, "closed": True, "selected": "1"}


def test_numbers_select_then_enter_reviews_and_only_an_explicit_send_submits_the_canonical_answer(tmp_path: Path) -> None:
    second = QUESTION | {"answer": "https://x.slack.com/archives/D1/p1794382453859300", "title": "Shard it?", "card": QUESTION["card"] | {"title": "Shard it?"}}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(150);
const press = (key) => document.body.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
const title = () => document.querySelector('#item-title').textContent;
press('j');
await wait(50);
const stepped = [title()];
press('k');
await wait(50);
stepped.push(title());
const sent = [];
const original = fetch;
window.fetch = (url, options) => url === '/api/answer' ? (sent.push(JSON.parse(options.body).label), new Promise(() => {})) : original(url, options);
press('2');
const selected = { title: title(), sent: [...sent] };
press('Enter');
const dialog = document.querySelector('#decision-review');
const reviewed = { open: dialog.open, exact: dialog.querySelector('[data-label]').dataset.label, sent: [...sent], focused: document.activeElement.textContent };
dialog.querySelector('[data-label]').click();
await wait(50);
done({ stepped, selected, reviewed, sent });
""", board(queue=[QUESTION, second]))
    assert seen == {"stepped": ["Shard it?", "Raise the limit?"], "selected": {"title": "Raise the limit?", "sent": []}, "reviewed": {"open": True, "exact": "Shard it", "sent": [], "focused": "Keep thinking"}, "sent": ["Shard it"]}


def test_a_pr_with_no_card_takes_approve_from_its_key_and_the_queue_lists_by_rank_and_steps_to_one_project(tmp_path: Path) -> None:
    url = "https://github.com/a/b/pull/7"
    tap = {
        "kind": "tap", "tasks": ["o1"], "url": url, "pr": url, "title": "fix the lock", "answer": None, "thread": None, "card": None, "since": None, "holds": 0, "blocks": [],
        "project": "Drive", "workstream": "Dashboard", "rank_why": "lands 1 PR; waiting since Nov 10 11:00 PM", "prs": [{"url": url, "title": "fix the lock", "state": "open", "checks": "pass", "review": "none", "tap": None}],
    }
    other = QUESTION | {"project": "Iris", "workstream": None, "rank_why": "waiting since Nov 10 10:00 PM"}
    review = [
        RAIL | {"key": "a/b#7", "title": "fix the lock", "url": url, "head": "abc", "group": "you", "feature": "o1", "feature_title": "", "detail": "green, no review yet", "checks": "pass",
                "review": "none", "tap": None, "stage": None, "since": None, "updated": None, "card": None, "answer": None, "thread": None, "approve_waits": "its review reads none"}
    ]
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const rows = () => [...document.querySelectorAll('#main .queue-list .qrow')].map((row) => row.dataset.rank);
const listed = rows();
const chips = [...document.querySelectorAll('#main .scope-projects a')].map((chip) => chip.textContent);
const press = (key) => document.body.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
press(']');
await wait(100);
const scoped = { hash: location.hash, title: document.querySelector('#item-title').textContent, listed: rows().length };
const sent = [];
const real = window.fetch;
window.fetch = (url, options) => (url === '/api/tap' ? (sent.push(JSON.parse(options.body)), new Promise(() => {})) : real(url, options));
const waits = document.querySelector('#main .pr-actions .meta').textContent;
press('a');
await wait(50);
done({ listed, chips, scoped, waits, sent });
""", board(queue=[tap, other], review=review))
    assert seen == {
        "listed": ["1", "2"],
        "chips": ["All projects2", "Drive1", "Iris1"],
        "scoped": {"hash": "#/queue/all?project=Drive", "title": "Fix the lock", "listed": 0},
        "waits": "After your Approve it still waits: its review reads none",
        "sent": [{"pr": "a/b#7", "label": "Approve", "head": "abc"}],
    }


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_queue_scope_nests_workstreams_in_the_project_keeps_counts_and_resets_without_losing_the_kind_filter(tmp_path: Path, size: str) -> None:
    items = [
        QUESTION | {"answer": f"https://x.slack.com/archives/D1/p1794382453859{n:03}", "project": project, "workstream": stream}
        for n, (project, stream) in enumerate([("Drive", "Dashboard"), ("Drive", "Jobs"), ("Iris", "Desktop")])
    ]
    seen = browse(tmp_path, """
location.hash = '#/queue/question';
await wait(100);
const scope = () => document.querySelector('#queue-scope');
const open = () => scope().querySelector('summary').click();
const choose = async (label) => {
  [...scope().querySelectorAll('a')].find((a) => a.firstElementChild.textContent === label).click();
  await wait(100);
};
const onTop = () => [...scope().querySelectorAll('.scope-panel a')].every((a) => {
  const box = a.getBoundingClientRect();
  return a.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2));
});
const state = () => ({
  open: scope().open,
  label: scope().querySelector('.scope-label').textContent,
  count: scope().querySelector('summary .n').textContent,
  streams: [...scope().querySelectorAll('.scope-streams a')].map((a) => a.textContent),
});
const initial = state();
open();
await choose('Drive');
const project = { ...state(), onTop: onTop() };
const panel = scope().querySelector('.scope-panel').getBoundingClientRect();
const inside = panel.left >= 0 && panel.right <= innerWidth;
const focused = document.activeElement.textContent;
await choose('Dashboard');
const stream = state();
open();
await choose('All workstreams');
const whole = state();
open();
await choose('Iris');
const single = state();
await push({ ...MODEL, queue: [...MODEL.queue, { ...MODEL.queue[2], answer: 'https://x.slack.com/archives/D1/p1794382453859309' }] });
const updated = state();
document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
const escaped = { open: scope().open, focused: document.activeElement === scope().querySelector('summary') };
open();
await choose('All projects');
const reset = { ...state(), filter: location.hash.split('/')[2], focused: document.activeElement === scope().querySelector('summary') };
done({ initial, project, inside, focused, stream, whole, single, updated, escaped, reset });
""", board(queue=items), size)
    assert seen == {
        "initial": {"open": False, "label": "All projects", "count": "3", "streams": []},
        "project": {"open": True, "label": "Drive", "count": "2", "streams": ["All workstreams2", "Dashboard1", "Jobs1"], "onTop": True},
        "inside": True,
        "focused": "All workstreams2",
        "stream": {"open": False, "label": "Drive/Dashboard", "count": "1", "streams": ["All workstreams2", "Dashboard1", "Jobs1"]},
        "whole": {"open": False, "label": "Drive", "count": "2", "streams": ["All workstreams2", "Dashboard1", "Jobs1"]},
        "single": {"open": True, "label": "Iris", "count": "1", "streams": ["All workstreams1", "Desktop1"]},
        "updated": {"open": True, "label": "Iris", "count": "2", "streams": ["All workstreams2", "Desktop2"]},
        "escaped": {"open": False, "focused": True},
        "reset": {"open": False, "label": "All projects", "count": "4", "streams": [], "filter": "question", "focused": True},
    }


def test_jump_lists_each_pr_once_finds_it_by_number_or_repo_marks_what_matched_and_counts_what_it_leaves_out(tmp_path: Path) -> None:
    stack = [{"url": f"https://github.com/a/b/pull/{n}", "title": f"Fix: the lock, part {n}", "summary": "", "refresher": "", "show_me": "", "head": "abc"} for n in (101, 102)]
    tap = {
        "kind": "tap", "tasks": [], "answer": "https://x.slack.com/archives/D1/p1794382453859300", "url": "https://x.slack.com/archives/D1/p1794382453859300", "title": "Fix: the lock", "since": None,
        "links": [], "thread": None, "holds": 0, "blocks": [], "card": {"title": "Approve the lock?", "options": ["Approve", "Hold"], "recommended": "Approve", "prs": stack},
    }
    review = [
        RAIL | {"key": f"a/b#{n}", "title": title, "url": f"https://github.com/a/b/pull/{n}", "head": "abc", "group": "you", "feature": "o1", "feature_title": "ship the fix", "detail": "",
         "checks": "pass", "review": "approved", "tap": None, "stage": None, "since": None, "updated": None, "card": None, "answer": None, "thread": None}
        for n, title in ((101, "Fix: the lock, part 101"), (102, "Fix: the lock, part 102"), (103, "New: export the board"))
    ]
    lanes = [board()["work"][0] | {"id": f"j{n}", "goal": f"lane {n}"} for n in range(45)]
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
await wait(100);
const find = document.querySelector('#jumpfind');
const typed = async (text) => {
  find.value = text;
  find.dispatchEvent(new Event('input'));
  await wait(50);
  const rows = [...document.querySelectorAll('#jumplist li')];
  return { rows: rows.slice(0, 3).map((row) => [row.querySelector('em').textContent, row.querySelector('.jump-title').textContent, row.querySelector('code')?.textContent ?? '']),
    shown: rows.length, marks: [...document.querySelectorAll('#jumplist mark')].map((mark) => mark.textContent), note: document.querySelector('#jumpnote').textContent };
};
const number = await typed('101');
const once = await typed('lock');
const many = await typed('lane');
const none = await typed('zzz');
const nothing = { ...none, active: find.getAttribute('aria-activedescendant') };
const repo = await typed('a/b');
const roles = { list: document.querySelector('#jumplist').getAttribute('role'), rows: [...document.querySelectorAll('#jumplist li')].map((row) => [row.getAttribute('role'), row.getAttribute('aria-selected')]), active: find.getAttribute('aria-activedescendant') };
find.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
await wait(50);
const down = { active: find.getAttribute('aria-activedescendant'), selected: [...document.querySelectorAll('#jumplist [aria-selected="true"]')].map((row) => row.id), focus: document.activeElement.id };
done({ number, once, many: { shown: many.shown, note: many.note }, nothing, repo, roles, down });
""", board(queue=[tap], review=review, work=lanes))
    pull = "Pull request"
    assert seen["number"] == {"rows": [[pull, "Fix: the lock", "a/b#101 +1"]], "shown": 1, "marks": ["101"], "note": "1 match"}
    assert seen["once"] == {"rows": [[pull, "Fix: the lock", "a/b#101 +1"]], "shown": 1, "marks": ["lock"], "note": "1 match"}
    assert seen["many"] == {"shown": 40, "note": "The first 40 of 45 matches. Type more to narrow them."}
    assert seen["nothing"] == {
        "rows": [], "shown": 0, "marks": [], "active": None,
        "note": "Nothing matches “zzz”. Jump finds a screen, a question, a pull request by its title, number or repo, a task by its title or order id, or a job by its id.",
    }
    assert seen["repo"]["rows"] == [[pull, "Fix: the lock", "a/b#101 +1"], [pull, "New: export the board", "a/b#103"]]
    assert seen["repo"]["marks"] == ["a/b", "a/b"]
    assert seen["roles"] == {"list": "listbox", "rows": [["option", "true"], ["option", "false"]], "active": "jump-0"}
    assert seen["down"] == {"active": "jump-1", "selected": ["jump-1"], "focus": "jumpfind"}


def test_a_lane_file_reads_in_the_pages_own_prose_under_one_title_and_hands_focus_back_on_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brief = tmp_path / "j0-brief.md"
    brief.write_text("Order: keep <b>rows</b> in place\n\n## Plan\n\n- Patch the rows\n- Measure the scroll\n\n| Step | File |\n| --- | --- |\n| Patch | `list.js` |\n")
    goal = "<img src=x onerror=alert(1)> the lock"
    job = {"goal": goal, "state": "waiting", "said": None, "updated": "2026-11-11T00:00:00-08:00", "where": "remote", "agent": None, "session": None, "questions": [], "brief": str(brief), "report": None, "repro": None}
    monkeypatch.setattr(dashboard.state, "load", lambda name: {"jobs": {"j0": job}})
    lane = board()["work"][0] | {"goal": goal, "files": ["brief"]}
    seen = browse(tmp_path, """
location.hash = '#/work/Platform/SandSQL/j0';
await wait(150);
const button = document.querySelector('#main .lane-sheet [data-file="j0/brief"]');
button.focus();
button.click();
await wait(150);
const drawn = (at) => ({ titles: [...at.querySelectorAll('h1, h2, .dr-bar b')].map((title) => title.textContent), cells: [...at.querySelectorAll('.reader-body td')].map((cell) => cell.textContent),
  items: [...at.querySelectorAll('.reader-body li')].map((item) => item.textContent), markup: at.querySelectorAll('.reader-body b, img').length });
const reader = document.querySelector('#reader');
const opened = { open: reader.open, frames: reader.querySelectorAll('iframe').length, tab: reader.querySelector('a.ext').getAttribute('href'), ...drawn(reader) };
reader.close();
await wait(50);
const back = document.activeElement.dataset.file;
location.hash = '#/file/j0/brief';
await wait(150);
done({ opened, back, tab: drawn(document.querySelector('#main')) });
""", board(work=[lane]))
    assert seen == {
        "opened": {"open": True, "frames": 0, "tab": "/#/file/j0/brief", "titles": [f"j0 brief: {goal}"], "cells": ["Patch", "list.js"], "items": ["Patch the rows", "Measure the scroll"], "markup": 0},
        "back": "j0/brief",
        "tab": {"titles": [f"j0 brief: {goal}"], "cells": ["Patch", "list.js"], "items": ["Patch the rows", "Measure the scroll"], "markup": 0},
    }


def test_a_long_card_link_in_history_wraps_on_a_phone_instead_of_widening_the_page(tmp_path: Path) -> None:
    title = "Should the customer and system data servers turn lazy too once the lazy setting goes away for good?"
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": f"Waits on [{title}](#/queue/question/1794382453.859209)", "detail": "", "url": None, "feature": "o1", "visuals": [], "prs": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
done({ wide: document.documentElement.scrollWidth, room: innerWidth });
""", board(history=[said]), "390,844")
    assert seen["wide"] <= seen["room"]


def test_work_flies_from_the_drive_to_a_lane_inside_one_canvas_and_esc_steps_back_one_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "load", lambda name: {"jobs": {}})
    iris = FEATURE | {"id": "o2", "title": "chats open on the Mac", "project": "Iris", "workstream": "Desktop app"}
    seen = browse(tmp_path, """
location.hash = '#/work';
await wait(200);
const canvas = document.querySelector('#main .canvas');
const look = () => ({ hash: location.hash, level: canvas.dataset.level, same: document.querySelector('#main .canvas') === canvas, replayed: document.querySelector('#main').classList.contains('arrived'), scale: nowCamera.at.k });
const overview = look();
canvas.querySelector('.face[href="#/work/Platform"]').click();
await wait(300);
const project = look();
canvas.querySelector('.face[href="#/work/Platform/SandSQL"]').click();
await wait(300);
const stream = look();
canvas.querySelector('.topo-node.lane[data-lane="j7"]').click();
await wait(300);
const lane = { ...look(), on: canvas.querySelector('.topo-node.lane.on').dataset.lane, sheet: canvas.querySelector('.lane-sheet h2').textContent };
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
await wait(300);
done({ overview, project, stream, lane, closed: { ...look(), sheet: !!canvas.querySelector('.lane-sheet') } });
""", board(status=[{"state": "running", "features": [FEATURE, iris]}]), "1200,600", "--force-prefers-reduced-motion")
    assert {name: {key: value for key, value in view.items() if key != "scale"} for name, view in seen.items()} == {
        "overview": {"hash": "#/work", "level": "all", "same": True, "replayed": False},
        "project": {"hash": "#/work/Platform", "level": "project", "same": True, "replayed": False},
        "stream": {"hash": "#/work/Platform/SandSQL", "level": "stream", "same": True, "replayed": False},
        "lane": {"hash": "#/work/Platform/SandSQL/j7", "level": "stream", "same": True, "replayed": False, "on": "j7", "sheet": "lane 7"},
        "closed": {"hash": "#/work/Platform/SandSQL", "level": "stream", "same": True, "replayed": False, "sheet": False},
    }
    assert seen["overview"]["scale"] < seen["project"]["scale"] < seen["stream"]["scale"]


def test_work_pans_on_a_drag_and_zooms_on_a_ctrl_wheel_or_a_pinch_and_a_key_still_opens_a_node_after_a_pinch(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/work/Platform';
await wait(200);
const viewport = document.querySelector('#main .viewport');
const { left, top } = viewport.getBoundingClientRect();
const [x, y] = [left + 40, top + 40];
const point = (type, id, px, kind = 'mouse') => viewport.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: id, pointerType: kind, isPrimary: id < 3, button: type === 'pointermove' ? -1 : 0, clientX: px[0], clientY: px[1] }));
const start = { ...nowCamera.at };
point('pointerdown', 1, [x, y]);
point('pointermove', 1, [x + 60, y + 30]);
point('pointerup', 1, [x + 60, y + 30]);
const dragged = { ...nowCamera.at };
viewport.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, ctrlKey: true, deltaY: -50, clientX: x, clientY: y }));
const wheeled = { ...nowCamera.at };
point('pointerdown', 2, [x, y], 'touch');
point('pointerdown', 3, [x + 100, y], 'touch');
point('pointermove', 3, [x + 150, y], 'touch');
point('pointerup', 3, [x + 150, y], 'touch');
point('pointerup', 2, [x, y], 'touch');
const pinched = nowCamera.at.k / wheeled.k;
const pinchedAt = location.hash;
document.querySelector('#main .face[href="#/work/Platform/SandSQL"]').click();
await wait(100);
done({ pinchedAt, keyed: location.hash, panned: [(start.x - dragged.x) * start.k, (start.y - dragged.y) * start.k, dragged.k / start.k], wheeled: wheeled.k / dragged.k, pinched });
""", board(), "1200,600", "--force-prefers-reduced-motion")
    assert (seen["pinchedAt"], seen["keyed"]) == ("#/work/Platform", "#/work/Platform/SandSQL")
    assert seen["panned"] == pytest.approx([60, 30, 1])
    assert seen["wheeled"] == pytest.approx(math.exp(0.5))
    assert seen["pinched"] == pytest.approx(1.5)


def test_a_workstream_node_says_what_its_lanes_do_in_plain_words_with_one_tone_per_state_and_one_node_per_name_however_spelled(tmp_path: Path) -> None:
    orders = {
        "o1": order("draw the canvas", ["j1", "j2", "j3"], project="Drive tooling", workstream="jobs", updated="2026-11-11T09:00:00-08:00"),
        "o2": order("fold the names", ["j4"], project="Drive tooling", workstream="Jobs", updated="2026-11-11T08:00:00-08:00"),
    }
    jobs = {
        "j1": job("o1", "running", "DONE ccx v0.95.56 installed (Homebrew) and its long return goes on past any node") | {"updated": fresh(1)},
        "j2": job("o1", "running") | {"updated": fresh(120)},
        "j3": job("o1", "built", "BUILT demo-branch") | {"updated": fresh(5)},
        "j4": job("o2", "waiting", kind="reader") | {"updated": fresh(5)},
    }
    shown = view.board(drive_of(orders, jobs, []), found(), "live", [], state.parse_time("2026-11-11T10:00:00-08:00"), set(), {})
    seen = browse(tmp_path, """
location.hash = '#/work/Drive%20tooling';
await wait(200);
const nodes = [...document.querySelectorAll('#main .stage.here > .topo-node')];
const tones = (selector) => [...new Set(nodes.flatMap((node) => [...node.querySelectorAll(selector)].map((each) => each.className)))].sort();
done({ names: nodes.map((node) => node.querySelector('.face b').textContent), doing: nodes.map((node) => [...node.querySelectorAll('.doing span')].map((part) => part.textContent)), words: tones('.doing span'), dashes: tones('.satellites i'), raw: nodes.some((node) => /DONE|BUILT/.test(node.textContent)) });
""", shown | {"code": "", "pid": 1, "links": [], "repo": "", "pages": []})
    assert seen == {
        "names": ["jobs ›"],
        "doing": [["1 working", "1 quiet", "1 waiting", "1 built"]],
        "words": ["t-bad", "t-doing", "t-waiting", "t-you"],
        "dashes": ["t-bad", "t-doing", "t-waiting", "t-you"],
        "raw": False,
    }


def test_a_workstreams_lanes_are_nodes_on_the_map_and_the_filter_keeps_those_that_match_and_grows_them(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/work/Platform/SandSQL';
await wait(200);
const canvas = document.querySelector('#main .canvas');
const room = canvas.querySelector('.viewport').getBoundingClientRect();
const look = () => {
  const lanes = [...canvas.querySelectorAll('.topo-node.lane')];
  const boxes = lanes.map((lane) => lane.getBoundingClientRect());
  return { lanes: lanes.map((lane) => lane.dataset.lane), inside: boxes.every((box) => box.left >= room.left - 1 && box.right <= room.right + 1 && box.top >= room.top - 1 && box.bottom <= room.bottom + 1), width: boxes[0].width };
};
const all = { ...look(), scrollers: canvas.querySelectorAll('.lane-cards, .lane-zoom').length };
const box = canvas.querySelector('[data-lane-filter]');
box.focus();
box.value = 'j3';
box.dispatchEvent(new Event('input', { bubbles: true }));
await wait(200);
done({ all, kept: { ...look(), hub: canvas.querySelector('.stage.lanes .hub small').textContent, typing: document.activeElement === canvas.querySelector('[data-lane-filter]') } });
""", board(), "1200,600", "--force-prefers-reduced-motion")
    assert (len(seen["all"]["lanes"]), seen["all"]["inside"], seen["all"]["scrollers"]) == (40, True, 0)
    assert seen["kept"]["lanes"] == ["j3", *(f"j{n}" for n in range(30, 40))]
    assert (seen["kept"]["inside"], seen["kept"]["hub"], seen["kept"]["typing"]) == (True, "11 of 40 lanes match", True)
    assert seen["kept"]["width"] > seen["all"]["width"]


def test_a_lanes_inspector_shows_its_newest_progress_line_and_every_file_the_drive_keeps_for_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_root: Path) -> None:
    held = {
        "j1": {"state": "built", "said": "PUSHED https://github.com/a/b/pull/7", "where": "remote", "report": None, "updated": "2026-11-11T00:00:00-08:00"},
        "j2": {"state": "waiting", "said": None, "where": "remote", "report": None, "updated": "2026-11-11T00:00:00-08:00"},
    }
    monkeypatch.setattr(state, "load", lambda name: {"jobs": held})
    shown = board()
    shown["work"][1] |= {"state": "built", "where": "remote", "files": ["brief", "report"]}
    shown["work"][2] |= {"state": "waiting", "where": "remote"}
    seen = browse(tmp_path, """
const sheet = async (id) => {
  location.hash = `#/work/Platform/SandSQL/${id}`;
  await wait(300);
  const at = document.querySelector('#main .lane-sheet');
  return { said: at.querySelector('.lane-said')?.textContent ?? null, links: [...at.querySelectorAll('.lane-said a')].map((link) => link.href), notes: [...at.querySelectorAll('p.meta')].map((note) => note.textContent), files: [...at.querySelectorAll('[data-file]')].map((file) => file.textContent) };
};
done({ sent: await sheet('j1'), silent: await sheet('j2') });
""", shown)
    assert (seen["sent"]["said"], seen["sent"]["links"]) == ("PUSHED #7", ["https://github.com/a/b/pull/7"])
    assert any(note.startswith("The line it last sent, ") for note in seen["sent"]["notes"])
    assert seen["sent"]["files"] == ["Brief", "Report"]
    assert seen["silent"]["said"] is None
    assert "j2 has said nothing yet: it sent no line, kept no report, and this Mac holds no transcript of it." in seen["silent"]["notes"]


def test_a_packet_runs_only_on_the_wires_of_a_lane_whose_record_changed_and_never_while_paused(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
localStorage.removeItem('motion');
location.hash = '#/work/Platform/SandSQL';
await wait(200);
const packets = () => [...document.querySelectorAll('#main .packets .packet')].map((packet) => packet.dataset.wire).sort();
const moved = (id, at) => ({ ...MODEL, at, work: MODEL.work.map((lane) => (lane.id === id ? { ...lane, updated: at } : lane)) });
const first = packets();
await push(moved('j5', '2026-11-11T00:01:00-08:00'));
await wait(150);
const sent = packets();
await wait(1600);
document.querySelector('#main [data-motion]').click();
await push(moved('j6', '2026-11-11T00:02:00-08:00'));
await wait(150);
done({ first, sent, paused: packets(), legend: document.querySelector('#main .map-legend').textContent });
""", board())
    assert seen["first"] == []
    assert seen["sent"] == ["Platform", "Platform/SandSQL", "Platform/SandSQL/j5"]
    assert seen["paused"] == []
    assert "decoration" not in seen["legend"]
    assert "A packet is a lane's record reaching the coordinator, or the coordinator starting a lane" in seen["legend"]


def test_work_fits_a_phone_with_no_node_clipped_or_overlapping_at_any_level(tmp_path: Path) -> None:
    names = ["Memory", "Search", "Dashboard", "Drive tooling", "Billing", "Docs", "Onboarding", "Alerts", "Auth", "Imports", "Mobile", "Reports", "Storage", "Sync"]
    features = [FEATURE, *(FEATURE | {"id": f"o{i + 2}", "project": name, "workstream": "Main"} for i, name in enumerate(names))]
    shown = board(status=[{"state": "running", "features": features}])
    for i, lane in enumerate(shown["work"]):
        lane["feature"] = features[i % 8]["id"]
    seen = browse(tmp_path, """
const check = async (hash) => {
  location.hash = hash;
  await wait(300);
  const room = document.querySelector('#main .viewport').getBoundingClientRect();
  const boxes = [...document.querySelectorAll('#main .stage.here > .topo-node, #main .stage.here > .hub')].map((el) => el.getBoundingClientRect());
  const over = (el) => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
  return {
    overlaps: boxes.flatMap((a, i) => boxes.slice(i + 1).filter((b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1)).length,
    outside: boxes.filter((box) => box.left < room.left - 1 || box.right > room.right + 1).length,
    clipped: [...document.querySelectorAll('#main .stage.here > .topo-node > .face, #main .stage.here > .topo-node.lane')].filter(over).length,
    wider: document.documentElement.scrollWidth > innerWidth,
  };
};
done({ all: await check('#/work'), project: await check('#/work/Platform'), lanes: await check('#/work/Platform/SandSQL') });
""", shown, "390,844", "--force-prefers-reduced-motion")
    assert seen == {level: {"overlaps": 0, "outside": 0, "clipped": 0, "wider": False} for level in ("all", "project", "lanes")}


def test_map_context_explains_the_map_and_the_footer_counts_its_job_records_and_registered_agents(tmp_path: Path) -> None:
    shown = board()
    shown["work"][1]["agent"] = shown["work"][0]["agent"]
    seen = browse(tmp_path, """
location.hash = '#/work';
await wait(200);
const foot = document.querySelector('#main .topo-label').textContent;
document.querySelector('#main [data-map-context]').click();
await wait(50);
const drawer = document.querySelector('#drawer');
done({ foot, open: drawer.open, title: drawer.querySelector('.dr-bar b').textContent, lanes: drawer.querySelector('.map-context').textContent.includes('40 lanes running, waiting or built') });
""", shown)
    assert seen == {"foot": "40 active job records · 39 registered agents", "open": True, "title": "A map of the registered work", "lanes": True}


def test_work_and_now_list_the_same_projects(tmp_path: Path) -> None:
    iris = FEATURE | {"id": "o2", "title": "chats open on the Mac", "project": "Iris", "workstream": "Desktop app"}
    loose = FEATURE | {"id": "o3", "title": "a loose end", "project": None, "workstream": None}
    seen = browse(tmp_path, """
location.hash = '#/now';
await wait(200);
const now = [...document.querySelectorAll('#main .region-name b')].map((name) => name.textContent).sort();
location.hash = '#/work';
await wait(200);
done({ now, work: [...document.querySelectorAll('#main .stage.here > .topo-node .face b')].map((name) => name.textContent.replace(' ›', '')).sort() });
""", board(status=[{"state": "running", "features": [FEATURE, iris, loose]}]))
    assert seen == {"now": ["Iris", "Platform", "Unfiled"], "work": ["Iris", "Platform", "Unfiled"]}


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



def test_a_long_message_he_was_sent_folds_to_four_lines_in_history_until_explain_more_opens_it(tmp_path: Path) -> None:
    text = " ".join(f"Line {n} of a message the drive sent you." for n in range(60))
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(200);
const fold = () => document.querySelector('#main li[data-kind="said"] .fold');
const em = parseFloat(getComputedStyle(fold()).fontSize);
const folded = { cut: fold().scrollHeight > fold().clientHeight, lines: fold().clientHeight <= 6.5 * em, more: document.querySelector('#main li[data-kind="said"] .fold-more').checkVisibility() };
document.querySelector('#main li[data-kind="said"] .fold-more').click();
await wait(100);
done({ folded, opened: fold().classList.contains('open') && fold().scrollHeight <= fold().clientHeight + 2 });
""", board(history=[{"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": text, "detail": "", "url": None, "feature": "o1", "visuals": [], "prs": []}]))
    assert seen == {"folded": {"cut": True, "lines": True, "more": True}, "opened": True}


def test_a_message_draws_its_bold_lines_as_headings_and_two_or_more_labeled_lines_as_a_labeled_list(tmp_path: Path) -> None:
    text = "**What shipped**\nFix: the importer skips rows it already wrote\nWhy: a retry wrote each row twice\n\n**What waits on you**\n- Pick the retry limit\n- Tap the docs PR\n\nNote: one labeled line stays a sentence"
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(200);
const row = document.querySelector('#main li[data-kind="said"] .said-text');
const texts = (selector) => [...row.querySelectorAll(selector)].map((el) => el.textContent.trim());
done({ headings: texts('h4.hd'), keys: texts('dl.keyed dt'), said: texts('dl.keyed dd'), items: texts('li'), lone: texts('p') });
""", board(history=[{"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": text, "detail": "", "url": None, "feature": "o1", "visuals": [], "prs": []}]))
    assert seen == {
        "headings": ["What shipped", "What waits on you"],
        "keys": ["Fix", "Why"],
        "said": ["the importer skips rows it already wrote", "a retry wrote each row twice"],
        "items": ["Pick the retry limit", "Tap the docs PR"],
        "lone": ["Note: one labeled line stays a sentence"],
    }

ARCH = {
    "arch": 1, "name": "drive", "title": "The dashboard reads the drive",
    "nodes": [
        {"id": "page", "kind": "app", "name": "Dashboard", "role": "draws cards"},
        {"id": "server", "kind": "service", "name": "Server", "role": "serves the model"},
        {"id": "state", "kind": "store", "medium": "disk", "name": "drive.json", "role": "keeps state"},
    ],
    "edges": [{"from": "page", "to": "server", "label": "reads /api/view"}, {"from": "server", "to": "state", "label": "loads"}],
}
DRAWN = [
    ("mermaid", "flowchart LR\n  page[Dashboard] --> server[Server] --> state[(drive.json)]"),
    ("dot", "digraph { rankdir=LR; page -> server -> state }"),
    ("chart", json.dumps({"data": {"values": [{"day": "Mon", "cards": 4}, {"day": "Tue", "cards": 7}]}, "mark": "bar", "encoding": {"x": {"field": "day", "type": "ordinal"}, "y": {"field": "cards", "type": "quantitative"}}})),
    ("compare", json.dumps({"options": [{"name": "Vendored", "recommended": True, "points": ["+ no network"]}, {"name": "CDN", "points": ["- a request per page"]}]})),
    ("arch", json.dumps(ARCH)),
    ("code:go", "func load(path string) (*Drive, error) {\n\treturn read(path)\n}"),
]
ALL_DRAWN = "await until(() => { const hosts = [...document.querySelectorAll('#main .visual-host')]; return hosts.length && hosts.every((host) => host.querySelector('svg, .visual-code, .visual-compare, .refused')); });"


def said(text: str = "the drive", visuals: list[tuple[str, str]] = DRAWN) -> dict:
    shown = [{"kind": kind, "source": source, "caption": kind, "picture": f"/{kind}.png"} for kind, source in visuals]
    return {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": text, "detail": "", "url": None, "feature": "o1", "visuals": shown, "prs": []}


def test_every_renderer_draws_from_the_dashboards_own_origin_and_asks_nothing_of_any_other(tmp_path: Path) -> None:
    seen = browse(tmp_path, f"""
location.hash = '#/history';
{ALL_DRAWN}
const hosts = [...document.querySelectorAll('#main .visual-host')];
done({{ drawn: hosts.map((host) => [host.dataset.visual, !host.querySelector('.refused')]), elsewhere: performance.getEntriesByType('resource').map((each) => each.name).filter((url) => new URL(url).origin !== location.origin) }});
""", board(history=[said()]), drawn=True)
    assert seen == {"drawn": [[kind, True] for kind, _ in DRAWN], "elsewhere": []}


def test_every_diagram_pans_zooms_by_wheel_and_pinch_fits_and_goes_full_screen_on_one_camera_and_arch_keeps_its_own(tmp_path: Path) -> None:
    body = "\n\n".join(f"```{kind}\n{source}\n```" for kind, source in DRAWN[:5])
    seen = browse(tmp_path, f"location.hash = '#/queue/all';\n{ALL_DRAWN}\n" + """
const k = (view) => Number(view.firstElementChild.style.getPropertyValue('--k'));
const press = (view, type, id, x, y) => { const r = view.getBoundingClientRect(); view.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: id, pointerType: 'touch', button: type === 'pointermove' ? -1 : 0, clientX: r.left + x, clientY: r.top + y })); };
document.querySelector('[data-story-evidence]').open = true;
const kinds = ['mermaid', 'dot', 'chart', 'compare'];
const seen = {};
for (const kind of kinds) {
  const host = document.querySelector(`#main .visual-host[data-visual="${kind}"]`);
  await until(() => host.querySelector('.frame'));
  const view = host.querySelector('.frame');
  host.scrollIntoView({ block: 'center' });
  const fit = k(view);
  const r = view.getBoundingClientRect();
  view.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, ctrlKey: true, deltaY: -40, clientX: r.left + 20, clientY: r.top + 20 }));
  const wheeled = k(view);
  press(view, 'pointerdown', 2, 20, 20);
  press(view, 'pointerdown', 3, 60, 20);
  press(view, 'pointermove', 3, 100, 20);
  const pinched = k(view);
  press(view, 'pointerup', 3, 100, 20);
  const placed = view.firstElementChild.style.transform;
  press(view, 'pointermove', 2, 5, 5);
  const panned = view.firstElementChild.style.transform !== placed && k(view) === pinched;
  press(view, 'pointerup', 2, 5, 5);
  host.querySelector('[data-cam="fit"]').click();
  const refit = k(view);
  seen[kind] = { zooms: wheeled > fit, pinches: pinched > wheeled, panned, fits: refit === fit, tools: [...host.querySelectorAll('.frame-tools button')].map((button) => button.getAttribute('aria-label')) };
}
document.querySelector('[data-story-evidence]').open = true;
const first = document.querySelector('#main .visual-host[data-visual="mermaid"] .frame');
press(first, 'pointerdown', 4, 20, 20);
press(first, 'pointerup', 4, 20, 20);
first.click();
for (const kind of kinds) {
  await until(() => document.querySelector(`#lightbox[open] .visual-host[data-visual="${kind}"] .frame`));
  const big = document.querySelector('#lightbox .frame').getBoundingClientRect();
  const stage = document.querySelector('#lightbox .lb-stage').getBoundingClientRect();
  seen[kind].fills = Math.round(big.width) === Math.round(stage.width) && Math.round(big.height) === Math.round(stage.height);
  document.querySelector('#lightbox [data-step="1"]').click();
}
const arch = document.querySelector('#main .visual-host[data-visual="arch"]');
seen.arch = { own: [...arch.querySelectorAll('.smap-tools [data-z]')].map((button) => button.dataset.z), ours: getComputedStyle(arch.closest('figure').querySelector(':scope > .enlarge')).display };
done(seen);
""", board(queue=[QUESTION | {"card": QUESTION["card"] | {"body": body}}]), "1440,1000", "--force-prefers-reduced-motion", drawn=True)
    framed = {"zooms": True, "pinches": True, "panned": True, "fits": True, "tools": ["Fit the whole diagram", "Zoom in", "Zoom out", "Full screen"], "fills": True}
    assert seen == {"mermaid": framed, "dot": framed, "chart": framed, "compare": framed, "arch": {"own": ["fit", "in", "out", "open"], "ours": "none"}}


def test_a_cited_line_peeks_highlighted_beside_its_cite_with_its_long_lines_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["import json", "", "def load(path: str) -> dict:", "    with open(path) as handle:", "        return json.load(handle)  # " + "a comment far wider than the peek " * 8, ""]
    monkeypatch.setattr(dashboard.github, "run", lambda cmd, **_: "\n".join(lines))
    seen = browse(tmp_path, """
location.hash = '#/history';
await until(() => document.querySelector('#main a[data-src]'));
const cite = document.querySelector('#main a[data-src]');
cite.scrollIntoView({ block: 'center' });
cite.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await until(() => document.querySelector('#srctip pre code span'));
const tip = document.querySelector('#srctip').getBoundingClientRect();
const at = cite.getBoundingClientRect();
const rows = [...document.querySelectorAll('#srctip pre > span')];
done({
  beside: tip.left >= at.right && tip.top <= at.bottom && tip.bottom >= at.top,
  marked: rows.filter((row) => row.classList.contains('hl')).map((row) => row.querySelector('i').textContent),
  keyword: document.querySelector('#srctip .hljs-keyword').textContent,
  wrapped: rows.find((row) => row.querySelector('i').textContent === '5').offsetHeight >= 2 * rows[0].offsetHeight,
  inside: rows.every((row) => row.scrollWidth <= row.clientWidth),
});
""", board(history=[said("load reads it at lib/drive/state.py:3-5 first", [])], repo="demo-org/demo-drive"), "1440,900", drawn=True)
    assert seen == {"beside": True, "marked": ["3", "4", "5"], "keyword": "import", "wrapped": True, "inside": True}


def test_a_fence_of_words_shows_as_a_code_embed_in_its_box_that_scrolls_and_copies(tmp_path: Path) -> None:
    words = "Approve ships it, Hold waits for the next build, and the card says which build it saw " * 3
    seen = browse(tmp_path, f"""
location.hash = '#/history';
{ALL_DRAWN}
done([...document.querySelectorAll('#main .visual-host[data-visual="code:text"]')].map((host) => {{
  const pre = host.querySelector('.visual-code');
  return {{ label: host.querySelector('.code-bar span').textContent, copy: host.querySelector('[data-copy]').textContent, boxed: getComputedStyle(pre).borderTopStyle, scrolls: pre.scrollWidth > pre.clientWidth, colored: host.querySelectorAll('[class^="hljs-"]').length, text: pre.textContent }};
}}));
""", board(history=[said(f"```text\n{words}\n```\n\n```\n{words}\n```", [])]), drawn=True)
    assert seen == [{"label": "text", "copy": "Copy", "boxed": "solid", "scrolls": True, "colored": 0, "text": words}] * 2


def test_a_cards_slack_picture_and_its_web_embed_are_drawn_from_one_source(tmp_path: Path, chrome: Chrome) -> None:
    flow = "flowchart LR\n  page[Dashboard] --> server[Server]"
    body = "\n".join(["Pick where it reads.", "", *picture.drawn(tmp_path / "pictures", ["The flow", "```mermaid", flow, "```"])])
    item = QUESTION | {"card": QUESTION["card"] | {"body": body}}
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await until(() => document.querySelector('[data-story-evidence]'));
document.querySelector('[data-story-evidence]').open = true;
await until(() => document.querySelector('#main .visual-host svg'));
const host = document.querySelector('#main .visual-host');
done({ kind: host.dataset.visual, source: host.dataset.source, nodes: [...host.querySelectorAll('.nodeLabel')].map((label) => label.textContent) });
""", board(queue=[item]), drawn=True)
    assert chrome.drawn == [("mermaid", seen["source"])]
    assert seen == {"kind": "mermaid", "source": f"{flow}\n", "nodes": ["Dashboard", "Server"]}


def test_an_install_reloads_the_page_only_when_its_site_files_changed_and_only_once_he_looks_away_from_it(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
let reloads = 0;
window.reloadPage = () => { reloads += 1; };
let hidden = false;
Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
const away = async (gone) => { hidden = gone; document.dispatchEvent(new Event('visibilitychange')); await wait(20); };
location.hash = '#/queue/all';
await wait(100);
await push({ ...MODEL, code: '/plugins/long-running/9.9.9/lib/drive', at: '2026-11-11T00:09:00-08:00' });
await away(true);
await away(false);
const same = { reloads, shown: !document.querySelector('#stale').hidden, drawn: document.querySelector('.qfoot').textContent.includes('12:09') };
await push({ ...MODEL, code: '/plugins/long-running/9.9.9/lib/drive', site: 'b2' });
const looking = { reloads, chip: document.querySelector('#stale').textContent, shown: !document.querySelector('#stale').hidden };
document.querySelector('#main .context-rail textarea').focus();
await away(true);
const typing = reloads;
document.activeElement.blur();
await away(false);
await away(true);
done({ same, looking, typing, away: reloads });
""", board(queue=[QUESTION], code="/plugins/long-running/1.0.1/lib/drive", site="a1"))
    assert seen == {
        "same": {"reloads": 0, "shown": False, "drawn": True},
        "looking": {"reloads": 0, "chip": "The dashboard was updated. Reload", "shown": True},
        "typing": 0,
        "away": 1,
    }


def test_a_reload_puts_him_back_where_he_was_reading(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(100);
placeAfterLoad = 900;
render();
await wait(50);
done({ top: scrollY, kept: placeAfterLoad });
""", board())
    assert seen == {"top": 900, "kept": None}


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



def test_his_asks_and_answers_list_each_decision_of_his_his_orders_taps_card_answers_recorded_answers_and_undos(tmp_path: Path) -> None:
    card = QUESTION["answer"]
    moments = [
        {"at": "2026-11-10T23:59:00-08:00", "kind": "undone", "text": "Raise the limit?", "detail": "Shard it", "url": card, "feature": None},
        {"at": "2026-11-10T23:58:00-08:00", "kind": "answered", "text": "Raise the limit?", "detail": "Shard it", "url": card, "feature": None},
        {"at": "2026-11-10T23:57:00-08:00", "kind": "answer", "text": "keep both until Friday", "detail": "", "url": card, "feature": None, "swears": 0},
        {"at": "2026-11-10T23:56:00-08:00", "kind": "tap", "text": "change 1", "detail": "Approve", "url": "https://github.com/a/b/pull/1", "feature": "o1"},
        {"at": "2026-11-10T23:55:00-08:00", "kind": "order", "text": "ship the fix", "detail": "", "url": None, "feature": "o1", "swears": 0},
        {"at": "2026-11-10T23:54:00-08:00", "kind": "merged", "text": "change 1", "detail": "", "url": "https://github.com/a/b/pull/1", "feature": "o1"},
    ]
    seen = browse(tmp_path, """
location.hash = '#/history?show=you';
await wait(150);
done({ on: document.querySelector('.subtabs a.on').textContent, rows: [...document.querySelectorAll('#main .history .happened .body > div:first-child')].map((row) => row.textContent) });
""", board(history=moments))
    assert seen == {
        "on": "Your asks and answers",
        "rows": [
            "You took back Shard it on Raise the limit?",
            "You picked Shard it on Raise the limit?",
            "You answered keep both until Friday",
            "You tapped Approve on change 1",
            "You asked ship the fix",
        ],
    }


def test_the_swear_chart_counts_the_swears_in_his_orders_and_his_answers_by_the_day_he_wrote_each(tmp_path: Path) -> None:
    moments = [
        {"at": "2026-11-10T23:00:00-08:00", "kind": "answer", "text": "keep both, damn it", "detail": "", "url": None, "feature": None, "swears": 2},
        {"at": "2026-11-10T22:00:00-08:00", "kind": "order", "text": "fix the damn thing", "detail": "", "url": None, "feature": "o1", "swears": 1},
        {"at": "2026-11-09T22:00:00-08:00", "kind": "order", "text": "Drive tooling: the shit the lanes say", "detail": "", "url": None, "feature": "o1", "swears": 0},
    ]
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(150);
const swears = document.querySelector('#main .history-stats > div:nth-child(2)');
done({ stat: [swears.querySelector('b').textContent, swears.querySelector('span').textContent], bars: [...document.querySelectorAll('#main .day-chart.swears .bar')].map((bar) => bar.getAttribute('aria-label')), note: document.querySelector('#main .day-chart.swears small').textContent });
""", board(history=moments))
    assert seen == {
        "stat": ["3", "swear words you wrote"],
        "bars": ["Nov 9: 0 swear words", "Nov 10: 3 swear words", "Nov 11: 0 swear words"],
        "note": "Swear words in your orders and answers, by the day you wrote each",
    }


def test_a_day_he_zooms_into_shows_its_hours_and_its_own_side_and_the_bar_tip_closes_as_he_goes(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(150);
const tip = document.querySelector('#srctip');
const bar = document.querySelector('#main .day-chart.merged .bar');
bar.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
const tipped = tip.matches(':popover-open');
bar.click();
await wait(200);
const bars = [...document.querySelectorAll('#main .day-chart.merged .bar')];
done({
  tipped,
  open: tip.matches(':popover-open'),
  hash: location.hash,
  bars: bars.length,
  links: bars.filter((each) => each.matches('a')).length,
  hours: [bars[0], bars[22]].map((each) => each.getAttribute('aria-label')),
  side: [...document.querySelectorAll('#main .history-side p:not(.rail-note)')].map((each) => each.textContent),
});
""", board())
    assert seen == {
        "tipped": True,
        "open": False,
        "hash": "#/history/2026-11-10",
        "bars": 24,
        "links": 0,
        "hours": ["12 AM: 0 PRs", "10 PM: 60 PRs"],
        "side": ["120 PRs merged, 0 orders from you and 120 moments in all.", "Busiest hour: 10 PM, 60 PRs merged."],
    }


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_the_history_numbers_sit_on_an_even_grid_in_full_contrast(tmp_path: Path, size: str) -> None:
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(150);
const stats = [...document.querySelectorAll('#main .history-stats > div')].map((each) => each.getBoundingClientRect());
done({ lefts: stats.map((each) => Math.round(each.left)), widths: stats.map((each) => Math.round(each.width)), color: getComputedStyle(document.querySelector('#main .big-date')).color === getComputedStyle(document.body).color });
""", board(), size)
    columns = sorted(set(seen["lefts"]))
    steps = {right - left for left, right in zip(columns, columns[1:])}
    assert (len(columns), max(steps) - min(steps) <= 1, len(set(seen["widths"])) == 1, seen["color"]) == (4 if size == "1440,900" else 2, True, True, True)


def test_a_picture_or_figure_sent_him_is_a_thumbnail_in_its_row_that_opens_in_the_lightbox(tmp_path: Path) -> None:
    visuals = [
        {"kind": "image", "source": "", "caption": "before", "picture": "/before.png"},
        {"kind": "mermaid", "source": "flowchart LR\n  run --> server", "caption": "the flow", "picture": "/flow.png"},
    ]
    said = {"at": "2026-11-10T23:59:30-08:00", "kind": "said", "text": "the flow, before and after", "detail": "", "url": None, "feature": "o1", "visuals": visuals, "prs": []}
    seen = browse(tmp_path, """
location.hash = '#/history';
await wait(150);
const row = document.querySelector('#main .happened[data-kind="said"]');
const figure = row.querySelector('.thumbs figure.visual');
figure.scrollIntoView({ block: 'center' });
await wait(50);
const box = figure.getBoundingClientRect();
document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2).click();
await wait(50);
const lightbox = document.querySelector('#lightbox');
done({ tall: row.getBoundingClientRect().height, open: lightbox.open, kind: lightbox.querySelector('.visual-host')?.dataset.visual, caption: lightbox.querySelector('.lb-caption').textContent });
""", board(history=[said]), "1440,900")
    assert seen["tall"] < 200
    assert {key: seen[key] for key in ("open", "kind", "caption")} == {"open": True, "kind": "mermaid", "caption": "the flow"}


def test_a_tap_on_a_fact_shows_its_tip_until_a_second_tap_a_tap_elsewhere_or_a_scroll_and_a_mouse_gets_hover_back(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const tip = document.querySelector('#srctip');
const fact = () => document.querySelector('#main .fact[data-tip]');
const open = () => tip.matches(':popover-open');
const tap = async (el) => {
  for (const kind of ['pointerover', 'pointerdown']) el.dispatchEvent(new PointerEvent(kind, { pointerType: 'touch', bubbles: true }));
  el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
  el.focus();
  el.click();
  await wait(50);
};
fact().scrollIntoView({ block: 'center' });
await wait(100);
await tap(fact());
const opened = { open: open(), says: tip.textContent.startsWith('Waiting on you for') };
await tap(fact());
const again = open();
await tap(fact());
await tap(document.querySelector('#item-title'));
const elsewhere = open();
await tap(fact());
window.dispatchEvent(new Event('scroll'));
await wait(50);
const scrolled = open();
fact().dispatchEvent(new PointerEvent('pointerover', { pointerType: 'mouse', bubbles: true }));
fact().dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
await wait(50);
done({ opened, again, elsewhere, scrolled, hovered: open() });
""", board(queue=[{"kind": "tap", "tasks": [], "url": "https://github.com/a/b/pull/7", "title": "fix the lock", "answer": None, "thread": None, "card": None, "since": "2026-11-10T23:00:00-08:00", "holds": 0, "blocks": []}]), "390,844")
    assert seen == {"opened": {"open": True, "says": True}, "again": False, "elsewhere": False, "scrolled": False, "hovered": True}


def test_on_a_phone_the_screens_sit_in_a_bottom_bar_every_target_is_a_thumb_wide_and_nothing_hides_under_the_bar(tmp_path: Path) -> None:
    tap = {"kind": "tap", "tasks": [], "url": "https://github.com/a/b/pull/7", "title": "fix the lock", "answer": None, "thread": None, "card": None, "since": None, "holds": 0, "blocks": []}
    incidents = [{"id": "o1", "title": "ship the fix", "created": "2026-11-10T20:00:00-08:00"}]
    looked = """
location.hash = '#/queue/all';
await wait(100);
const box = (el) => el.getBoundingClientRect();
const bar = box(document.querySelector('.side'));
const tabs = [...document.querySelectorAll('.side .tab')].map(box);
const targets = [...document.querySelectorAll('#main .counter button, .subtabs a, #main [data-prtab], #main .btn, #main .pr-picks button, #main .fold-more, #main .map-btn')];
toast('card', { label: 'Raise it', title: 'Raise the limit?', state: 'sent', undoUntil: new Date(Date.now() + 600000).toISOString() });
const shown = [...document.querySelectorAll('#toasts .toast :is(.btn, .toast-x)')];
window.scrollTo(0, document.documentElement.scrollHeight);
for (const slide of document.querySelector('#toasts .toast').getAnimations()) slide.finish();
done({
  bar: { bottom: Math.round(bar.bottom) === innerHeight, across: Math.round(bar.width) === innerWidth, row: new Set(tabs.map((tab) => Math.round(tab.top))).size },
  small: [...targets, ...shown].filter((el) => el.checkVisibility() && (box(el).height < 44 || box(el).width < 44)).map((el) => el.textContent.trim()),
  strip: new Set([...document.querySelectorAll('#alerts > *')].map((chip) => Math.round(box(chip).top))).size,
  gutter: innerWidth - Math.round(box(document.querySelector('#main .focus-card')).width),
  clear: { page: box(document.querySelector('#main').lastElementChild).bottom <= bar.top, toast: box(document.querySelector('#toasts .toast')).bottom <= bar.top },
});
"""
    model = board(queue=[tap, QUESTION], incidents=incidents)
    phone = browse(tmp_path / "phone", looked, model, "390,844")
    desk = browse(tmp_path / "desk", looked, model, "1440,900")
    assert phone == {"bar": {"bottom": True, "across": True, "row": 1}, "small": [], "strip": 1, "gutter": 32, "clear": {"page": True, "toast": True}}
    assert desk["bar"] == {"bottom": True, "across": False, "row": 5}


def test_an_answer_he_sent_comes_back_from_its_toast_or_its_history_line_while_cc_slack_shows_its_undo(state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    second = "https://x.slack.com/archives/D1/p1794382453859300"
    answered = {"at": "2026-11-10T23:59:30-08:00", "kind": "answered", "text": "Shard it?", "detail": "Shard it", "url": second, "feature": None, "undo_until": "2099-01-01T08:00:00Z"}
    reopened = []
    state.create(state.new_drive("demo", "coordinator", "s1", "/work", "D1"))
    monkeypatch.setattr(dashboard, "answer_card", lambda card, label, via: {"answered": True, "label": label, "undo_until": "2099-01-01T08:00:00Z"})
    monkeypatch.setattr(dashboard, "cc_slack", lambda *args: reopened.append(args[-1]) or {})
    monkeypatch.setattr(dashboard.view, "refresh_cards", lambda name: None)
    seen = browse(tmp_path, """
location.hash = '#/queue/all';
await wait(100);
const last = () => {
  const shown = [...document.querySelectorAll('#toasts .toast')].at(-1);
  return { about: shown.querySelector('span').textContent, said: shown.querySelector('.meta').textContent, acts: [...shown.querySelectorAll('a, button')].map((act) => act.textContent) };
};
document.querySelector('[data-story-pick="1"]').click();
document.querySelector('[data-story-review]').click();
document.querySelector('#decision-review [data-label]').click();
await wait(300);
const sent = { ...last(), choices: document.querySelectorAll('#main [data-story-pick]').length };
document.querySelector('#toasts [data-undo]').click();
await wait(300);
const restored = { ...last(), banner: document.querySelector('#main .restored')?.textContent, choices: document.querySelectorAll('#main [data-story-pick]').length, hash: location.hash };
location.hash = '#/history';
await wait(100);
document.querySelector('#main .happened[data-kind="answered"] [data-undo]').click();
await wait(300);
done({ sent, restored, history: { ...last(), hash: location.hash } });
""", board(queue=[QUESTION], history=[answered]))
    assert seen == {
        "sent": {"about": "Shard it · Raise the limit?", "said": "Sent", "acts": ["Undo", "×"], "choices": 0},
        "restored": {
            "about": "Shard it · Raise the limit?", "said": "Restored. This is being asked again.", "acts": ["Open it", "×"],
            "banner": "Restored. This is being asked again.", "choices": 2, "hash": "#/queue/all/1794382453.859209",
        },
        "history": {"about": "Shard it · Shard it?", "said": "Restored. This is being asked again.", "acts": ["Open it", "×"], "hash": "#/history"},
    }
    assert reopened == [QUESTION["answer"], second]
    assert list(state.read_events("demo")) == []


VISITS = ["#/queue/all", "#/now", "#/now/Platform/SandSQL/o1", "#/feature/o1", "#/work", "#/work/Platform/SandSQL", "#/work/Platform/SandSQL/j1", "#/history", "#/history/2026-11-09"]
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
    shown = view.board(drive_of(orders, jobs, prs), found([asked]), "live", events, state.parse_time("2026-11-11T10:00:00-08:00"), set(), {})
    return shown | {"code": "", "pid": 1, "links": [], "repo": "", "pages": []}


def test_each_screen_reads_the_same_requests_of_the_same_size_however_old_the_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_root: Path) -> None:
    monkeypatch.setattr(state, "load", lambda name: {"jobs": {"j1": job("o1", "running", "drawing it")}})
    seen = {n: browse(tmp_path / str(n), VISITED, aged(n)) for n in (1, 100, 1000)}
    assert {screen for screen, _, _ in seen[1]} == {"summary", *VISITS}
    assert seen[1] == seen[100] == seen[1000]


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_filtering_keeps_global_ranks_and_visible_reasons_without_shortening_the_question(tmp_path: Path, size: str) -> None:
    title = "Should we reserve capacity for writes so one team's reads cannot stop everyone else?"
    items = [
        QUESTION | {"answer": f"https://x.slack.com/archives/D1/p1794382453859{n:03}", "project": project, "workstream": "Reads", "title": title, "priority": {"incident": n == 1, "overdue": n == 3, "weight": n}}
        for n, project in enumerate(["Iris", "Platform", "Iris", "Platform"])
    ]
    seen = browse(tmp_path, """
location.hash = '#/queue/question?project=Platform';
await wait(150);
const rows = [...document.querySelectorAll('#main .queue-list .qrow')];
const read = rows.map((row) => ({ rank: row.dataset.rank, reasons: row.querySelector('.priority-chips').textContent, title: row.querySelector('.qrow-title').textContent, clipped: getComputedStyle(row.querySelector('.qrow-title')).textOverflow === 'ellipsis', links: row.querySelectorAll('a').length, buttons: row.querySelectorAll('button').length, tooltip: !!row.querySelector('[data-tip]') }));
const href = rows[1].querySelector('.qrow-main').getAttribute('href');
rows[1].querySelector('.qrow-main').click();
await wait(100);
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'q', bubbles: true }));
await wait(100);
const box = document.activeElement;
box.value = 'How does this affect writes?';
box.dispatchEvent(new Event('input', { bubbles: true }));
await push({ ...MODEL, at: new Date().toISOString() });
done({ read, draft: document.activeElement.value, asking: box === document.activeElement, opened: location.hash === href, selected: document.querySelector('.qrow.on a').getAttribute('href') === href, scoped: location.hash.includes('project=Platform'), overflow: document.documentElement.scrollWidth > innerWidth });
""", board(queue=items), size)
    assert seen == {
        "read": [
            {"rank": "2", "reasons": "#2IncidentUnblocks 1", "title": title, "clipped": False, "links": 1, "buttons": 0, "tooltip": False},
            {"rank": "4", "reasons": "#4Past dueUnblocks 3", "title": title, "clipped": False, "links": 1, "buttons": 0, "tooltip": False},
        ],
        "draft": "How does this affect writes?", "asking": True, "opened": True, "selected": True, "scoped": True, "overflow": False,
    }


@pytest.mark.parametrize("count", [1, 1000 if os.environ.get("FORGE_CI_AT_SCALE") == "1" else 100])
def test_queue_reads_one_focused_card_at_any_queue_size(tmp_path: Path, count: int) -> None:
    items = [QUESTION | {"answer": f"https://x.slack.com/archives/D1/p1794382453{n:06}"} for n in range(count)]
    seen = browse(tmp_path, """
await wait(150);
done(reads.filter((url) => url.startsWith('/api/card?')).length);
""", board(queue=items), before="""
const reads = [];
const fetchCard = window.fetch;
window.fetch = (url, ...args) => { reads.push(String(url)); return fetchCard(url, ...args); };
""")
    assert seen == 1


def test_live_project_reordering_keeps_the_focused_workstream_and_its_zoom_in_place(tmp_path: Path) -> None:
    iris = FEATURE | {"id": "o2", "project": "Iris", "workstream": "Desktop"}
    seen = browse(tmp_path, """
location.hash = '#/now/Platform/SandSQL';
await wait(800);
const target = () => document.querySelector('.stream.here');
const before = target().getBoundingClientRect();
const scale = nowCamera.at.k;
const queue = [{ ...MODEL.queue[0], project: 'Iris', tasks: ['o2'] }];
await push({ ...MODEL, queue });
await wait(100);
const after = target().getBoundingClientRect();
done({ sameScale: Math.abs(nowCamera.at.k - scale) < 0.001, samePlace: Math.abs(before.left - after.left) < 1 && Math.abs(before.top - after.top) < 1, visible: after.left < innerWidth && after.right > 0, hash: location.hash });
""", board(queue=[QUESTION | {"project": "Platform"}], status=[{"state": "running", "features": [FEATURE, iris]}]), "1440,900", "--force-prefers-reduced-motion")
    assert seen == {"sameScale": True, "samePlace": True, "visible": True, "hash": "#/now/Platform/SandSQL"}
