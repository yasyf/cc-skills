import datetime as dt
import json
import queue
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import server
from review_assistant import ReviewAssistant
from server import Drive, drive_prs, parse_plan, plan_prs, publish, step_rows, verdict_state

TAILNET_OWNER = "owner@example.test"
OWNER_WHOIS = {"Node": {"Name": "owners-mac."}, "UserProfile": {"LoginName": TAILNET_OWNER}}
TAGGED_WHOIS = {"Node": {"Name": "sprite.", "Tags": ["tag:sprite"]}, "UserProfile": {"LoginName": "tagged-devices"}}
TAGGED_AS_OWNER_WHOIS = {"Node": {"Name": "sprite.", "Tags": ["tag:sprite"]}, "UserProfile": {"LoginName": TAILNET_OWNER}}
OTHER_WHOIS = {"Node": {"Name": "laptop."}, "UserProfile": {"LoginName": "someone-else@example.test"}}
REFUSED_CALLERS = [json.dumps(TAGGED_WHOIS), json.dumps(TAGGED_AS_OWNER_WHOIS), json.dumps(OTHER_WHOIS), "not json", "null", json.dumps({"Node": {}}), subprocess.CalledProcessError(1, "tailscale", stderr="no match for IP"), subprocess.TimeoutExpired("tailscale", 60), FileNotFoundError(2, "No such file or directory", "tailscale")]
MODEL_KEY = "model-key-the-browser-never-sees"
MODEL_REPLY = b'data: {"choices": [{"delta": {"content": "hello"}}]}\n\ndata: [DONE]\n\n'
VERDICT = {"block": "b", "number": 7, "verdict": "approved", "head": "abc"}

NOW = dt.datetime(2026, 10, 27, 8, 0, tzinfo=dt.timezone.utc)
TREES = ["New tree", "Old tree", "Shared"]

DOC = """id: a5262e7
updated: 2026-10-27T06:30:00Z

# Progress

## Ordered plan of action (what lands, in what order)
1. **Now, no owner action needed.** The gateway batch (#42712, #42714) lands, then #43200.
2. **Taps he still owes:** the credential stack (#42788-#42790, #42737).
3. Step 11 cutover.

## Lanes live
- #49999 is not a plan step.
"""


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("#42712 and #42714", [42712, 42714]),
        ("(#42788-#42790, #42737)", [42788, 42789, 42790, 42737]),
        ("#42712 again #42712", [42712]),
        ("no pull requests", []),
    ],
)
def test_plan_prs_expands_ranges_once(text, numbers):
    assert plan_prs(text) == numbers


def test_parse_plan_reads_only_the_ordered_section():
    updated, steps = parse_plan(DOC)
    assert updated == "2026-10-27T06:30:00Z"
    assert [step["title"] for step in steps] == ["Now, no owner action needed", "Taps he still owes", "Step 11 cutover."]
    assert steps[0]["prs"] == [42712, 42714, 43200]
    assert steps[1]["prs"] == [42788, 42789, 42790, 42737]
    assert steps[2]["text"] == ""


def test_parse_plan_skips_a_doc_without_the_section():
    assert parse_plan("id: x\nupdated: now\n\n## Lanes live\n1. **A.** b\n") is None


def test_read_board_finds_a_verdict_keyed_by_the_bare_pr_number():
    row = {"number": 43211, "head": "8242887", "title": "T", "change": "C.", "plan": "P.", "risk": "R.", "review": "V.", "blocker": "B."}
    board = {
        "doc": {"blocks": [{"id": "card-merge", "type": "card", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "G", "summary": "S.", "prs": [row]}]}]}]},
        "interactions": {"triage": {"merge-prs": {"43211": {"verdict": "approved", "head": "8242887"}}}, "choices": {}},
    }
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(board)["rows"]
    assert rows[0]["block"] == "merge-prs"
    assert rows[0]["verdict"] == {"verdict": "approved", "head": "8242887"}


def card(block_id, *children, status=None):
    return {"id": block_id, "type": "card", **({"status": status} if status else {}), "children": [{"id": child_id, "type": kind} for kind, child_id in children]}


def board(*cards, retired=(), **answers):
    interactions = {"triage": {}, "choices": {}, "decisions": {}, "inputs": {}, "feedback": {}}
    for kind, entries in answers.items():
        interactions[kind] = entries
    return {"doc": {"blocks": list(cards)}, "interactions": interactions, "retired": list(retired)}


@pytest.mark.parametrize(
    ("store", "open_ids"),
    [
        (board(card("ask", ("choice", "pick"))), ["ask"]),
        (board(card("ask", ("choice", "pick")), choices={"pick": {"optionIds": ["a"]}}), []),
        (board(card("ask", ("choice", "pick")), choices={"pick": {"optionIds": [], "other": "both views"}}), []),
        (board(card("plan", ("approval", "ok")), decisions={"ok": {"verdict": "approved"}}), []),
        (board(card("shots", ("image", "phone"), status="open")), ["shots"]),
        (board(card("shots", ("image", "phone"), status="open"), feedback={"shots": [{"text": "evolutionary"}]}), []),
        (board(card("ask", ("choice", "pick")), retired=["ask"]), []),
        (board(card("notes", ("markdown", "body"))), []),
    ],
)
def test_a_question_he_answered_leaves_the_board_count(store, open_ids):
    assert [c["id"] for c in Drive({"board_url": "http://board/p/x"}, None).read_board(store)["cards"]] == open_ids


@pytest.mark.parametrize(
    ("row", "state"),
    [
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "bbbbbbb"}}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "aaaaaaa"}, "carried": ["aaaaaaa9"]}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "aaaaaaa"}}, "you"),
        ({"head": "bbbbbbb1", "verdict": None, "go_head": "bbbbbbb"}, "go"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "rejected", "head": "bbbbbbb"}}, "held"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "cleared"}, "go_head": "aaaaaaa"}, "you"),
    ],
)
def test_a_tap_at_the_head_or_a_carried_head_is_not_waiting_on_him(row, state):
    assert verdict_state(row) == state


def test_a_board_row_waits_on_him_only_at_the_pr_current_head():
    prs = {"42001": {"state": "open", "head": "bbbbbbb1", "test_state": "success"}, "42002": {"state": "open", "head": "ccccccc1", "test_state": "success"}}
    rows = [{"number": 42001, "head": "aaaaaaa1", "state": "you"}, {"number": 42002, "head": "ccccccc1", "state": "you"}]

    drive = drive_prs(prs, rows, [], {}, TREES, None, NOW, {})

    assert {square["number"]: square["state"] for square in drive["prs"]} == {42001: "work", 42002: "you"}


def test_a_square_tells_its_workers_time_from_the_record_stamp_whether_the_text_carries_no_clock_or_a_stale_one():
    prs = {"42001": {"state": "open", "head": "aaaaaaa1", "test_state": "success"}, "42002": {"state": "open", "head": "bbbbbbb1", "test_state": "success"}}
    records = [
        {"seq": 7, "lane": "fix-1", "kind": "head", "topic": "", "at": "2026-10-27T07:50:00Z", "text": "pushed #42001"},
        {"seq": 8, "lane": "fix-2", "kind": "head", "topic": "", "at": "2026-10-27T07:55:00Z", "text": "2:13 AM fix-2: pushed #42002"},
    ]

    drive = drive_prs(prs, [], records, {}, TREES, None, NOW, {})

    assert {square["number"]: square["worker"] for square in drive["prs"]} == {42001: {"lane": "fix-1", "at": "2026-10-27T07:50:00Z"}, 42002: {"lane": "fix-2", "at": "2026-10-27T07:55:00Z"}}


def test_a_head_the_landing_runner_admitted_shows_merging_or_runner_defect_with_its_age_never_waiting_on_him():
    """Coordinator #208560 item 4: "waiting on you" never lists a head the runner has admitted."""
    prs = {"42001": {"state": "open", "head": "aaaaaaa1", "test_state": "success"}, "42002": {"state": "open", "head": "bbbbbbb1", "test_state": "success"}, "42003": {"state": "open", "head": "ccccccc1", "test_state": "success"}}
    rows = [{"number": 42001, "head": "aaaaaaa1", "state": "you"}, {"number": 42002, "head": "bbbbbbb1", "state": "you"}, {"number": 42003, "head": "ccccccc1", "state": "you"}]
    admitted = {"42001": {"head": "aaaaaaa1", "since": "2026-10-27T07:40:00Z", "state": "merging"}, "42002": {"head": "bbbbbbb1", "since": "2026-10-27T07:20:00Z", "state": "defect", "why": "no branch recorded"}, "42003": {"head": "0000000", "since": "2026-10-27T07:20:00Z", "state": "merging"}}

    drive = drive_prs(prs, rows, [], {}, TREES, None, NOW, admitted)

    assert {square["number"]: (square["state"], square.get("since")) for square in drive["prs"]} == {42001: ("merging", "2026-10-27T07:40:00Z"), 42002: ("defect", "2026-10-27T07:20:00Z"), 42003: ("you", None)}


def test_the_ring_counts_exactly_the_green_squares():
    prs = {
        "42001": {"state": "landed"},
        "42002": {"state": "landed"},
        "42003": {"state": "landed"},
        "42004": {"state": "open", "test_state": "failure"},
        "42005": {"state": "open"},
        "42006": {"state": "closed-without-squash"},
        "42007": {"state": None},
    }
    trees = {"42001": ["Old tree", "New tree"], "42002": ["Shared"], "42004": ["Old tree"]}
    records = [{"lane": "p8a-step7", "at": "2026-10-27T07:50:00Z", "text": "pushed #42005", "topic": ""}]

    drive = drive_prs(prs, [], records, trees, TREES, None, NOW, {})

    squares = drive["prs"]
    assert drive["landed"] == sum(1 for square in squares if square["state"] == "landed") == 3
    assert drive["total"] == len(squares) == 5
    assert [square["tree"] for square in squares] == ["New tree", "Shared", "Shared", "Old tree", "Shared"]
    assert {square["number"]: square["state"] for square in squares}[42005] == "doing"
    assert drive["moving"] == 1


def test_each_failing_square_names_its_red_class_and_holder_and_the_drive_counts_reds_by_holder():
    prs = {"42001": {"state": "open", "test_state": "failure"}, "42002": {"state": "open", "test_state": "failure"}, "42003": {"state": "open", "test_state": "failure"}, "42004": {"state": "open", "test_state": "success"}}
    red = {"checks": "buildkite/test", "since": "2026-10-27T07:00:00Z", "classed_at": "", "action": ""}
    stack = {"at": "2026-10-27T08:00:00Z", "prs": [{"pr": "42001", "red": red | {"class": "flake", "owner": "red-sweep"}}, {"pr": "42002", "red": red | {"class": "unclassed", "owner": "api-lane"}}, {"pr": "42004"}]}

    drive = drive_prs(prs, [], [], {}, TREES, stack, NOW, {})

    assert {square["number"]: square.get("red", {}).get("owner") for square in drive["prs"]} == {42001: "red-sweep", 42002: "api-lane", 42003: None, 42004: None}
    assert drive["reds"] == {"count": 3, "unclassed": 2, "holders": {"red-sweep": 1, "api-lane": 1, "nobody": 1}}


def step(prs, lanes=(), tone="later"):
    return {"id": "s", "title": "T", "state": "S", "tone": tone, "blocker": None, "next": "N", "prs": list(prs), "lanes": list(lanes)}


@pytest.mark.parametrize(
    ("states", "lanes", "tone"),
    [
        ({1: "landed", 2: "landed"}, [], "done"),
        ({1: "landed", 2: "you"}, [], "you"),
        ({1: "doing", 2: "failing"}, [], "failing"),
        ({1: "work"}, ["p8a-step7"], "moving"),
        ({1: "work"}, [], "queued"),
        ({}, [], "later"),
    ],
)
def test_a_step_takes_its_tone_and_fill_from_the_drive_list(states, lanes, tone):
    drive = {"prs": [{"number": n, "state": state, "tree": "Shared", "worker": None} for n, state in states.items()]}
    workers = [{"lane": "p8a-step7", "at": "2026-10-27T07:55:00Z", "text": "pushed"}]

    row = step_rows([step([*states, 99], lanes)], drive, workers, NOW)[0]

    assert row["tone"] == tone
    assert [entry["number"] for entry in row["prs"]] == list(states)
    assert row["landed"] == sum(1 for state in states.values() if state == "landed")


def test_review_assistant_queues_each_request_attempt_once_and_only_his_comments(tmp_path):
    assistant = ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, "http://model")
    assistant.queues[7] = queue.Queue()
    request = {"type": "ai.request.created", "request": {"id": "1", "attempt": 1}}
    for event in (
        request,
        request,
        {"type": "ai.request.created", "request": {"id": "1", "attempt": 2}},
        {"type": "comment.created", "id": "c1", "comment": {"author": "claude"}},
        {"type": "comment.created", "id": "c2", "comment": {"author": "remote"}},
        {"type": "comment.created", "id": "c3", "comment": {"author": "user"}},
        {"type": "file.states"},
    ):
        assistant.offer(7, event)
    queued = [assistant.queues[7].get_nowait() for _ in range(assistant.queues[7].qsize())]
    assert [e.get("request", {}).get("attempt") or e["id"] for e in queued] == [1, 2, "c3"]


def test_a_cc_notes_doc_is_read_once_until_its_ref_moves(monkeypatch):
    refs = {"refs/cc-notes/docs/aaa1111": "oid-a1", "refs/cc-notes/docs/bbb2222": "oid-b1"}
    bodies = {"aaa1111": "id: aaa1111\nupdated: first\n\nRelease one", "bbb2222": DOC}
    shown = []

    def run(argv, cwd=None):
        assert cwd == "/repo"
        if argv[0] == "git":
            return "".join(f"{oid}\n" for ref, oid in sorted(refs.items()) if ref.startswith(argv[-1].rstrip("*")))
        if argv[1:3] == ["doc", "list"]:
            return "aaa1111\t2026-10-29\tprogress:x\tnot a plan\nbbb2222\t2026-10-29\tprogress:x\tplan\n"
        shown.append(argv[3])
        return bodies[argv[3]]

    monkeypatch.setattr("server.run", run)
    drive = Drive({"checkout": "/repo", "progress_label": "progress:x"}, None)
    for _ in range(3):
        assert drive.read_plan()[0] == "2026-10-27T06:30:00Z"
        assert drive.read_doc("aaa1111") == {"markdown": "Release one", "updated": "first"}
    assert shown == ["aaa1111", "bbb2222"]

    bodies["aaa1111"] = "id: aaa1111\nupdated: second\n\nRelease two"
    refs["refs/cc-notes/docs/aaa1111"] = "oid-a2"
    assert drive.read_doc("aaa1111") == {"markdown": "Release two", "updated": "second"}
    assert drive.read_plan()[0] == "2026-10-27T06:30:00Z"
    assert shown == ["aaa1111", "bbb2222", "aaa1111"]


def test_the_bus_alarm_beside_the_steps_is_the_drive_wide_stop_until_the_runner_clears_it(tmp_path, monkeypatch):
    """4:31 to 4:40 PM Oct 29 the bus hung and nothing on the dashboard said so (coordinator #190549)."""
    steps = tmp_path / "progress-desk" / "steps.json"
    steps.parent.mkdir()
    steps.write_text(json.dumps({"updated": "2026-10-30T13:00:00Z", "steps": [], "now": {"blocker": None, "release": {"name": "New code in the cloud"}}}))
    drive = Drive({"steps": str(steps)}, None)
    monkeypatch.setattr(drive, "board", lambda: {"rows": [], "cards": []})
    monkeypatch.setattr(drive, "check_gos", lambda rows, records: None)
    model = {"prs": {}, "records": [], "lanes": [], "trees": {}, "tree_names": TREES}
    assert drive.live(model)["now"] == {"blocker": None, "release": {"name": "New code in the cloud"}}

    alarm = {"since": "2026-10-30T13:31:00Z", "text": "BUS-DOWN since 6:31 AM: cci has not answered 2 probes in a row"}
    (tmp_path / "bus-down.json").write_text(json.dumps(alarm))
    assert drive.live(model)["now"] == {"blocker": alarm, "release": {"name": "New code in the cloud"}}


def test_a_build_failure_is_a_defect_only_when_the_next_build_fails_the_same_way(monkeypatch):
    """#278757: brew relinked ccn at 1:33 AM, one build hit the gap, and the dashboard posted a defect for a failure the next build no longer had."""
    drive = Drive({}, None)
    reported = []
    monkeypatch.setattr(drive, "report_defect", reported.append)
    missing = "FileNotFoundError: [Errno 2] No such file or directory: 'ccn'"
    drive.build_failed(missing)
    assert reported == []
    drive.build_failed(missing)
    drive.build_failed(missing)
    assert reported == [missing]


def test_the_serving_dashboard_replaces_a_retired_servers_record_with_its_own(tmp_path):
    """Coordinator #285830: dashboard/server.json still named the retired Oct 23 page on 127.0.0.1:8958 while the launchd dashboard served on 61210."""
    record = tmp_path / ".claude" / "scratch" / "d1" / "dashboard" / "server.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"drive": "d1", "pid": 68257, "host": "127.0.0.1", "port": 8958, "url": "http://127.0.0.1:8958/", "token": "old"}))

    assert publish({"drive": "d1", "host": "100.113.56.52", "port": 61210}, tmp_path, 22414, NOW) == record

    assert json.loads(record.read_text()) | {"script": ""} == {"drive": "d1", "pid": 22414, "host": "100.113.56.52", "port": 61210, "url": "http://100.113.56.52:61210/", "tailnet_url": "http://100.113.56.52:61210/", "script": "", "started_at": "2026-10-27T08:00:00Z"}
    assert [path.name for path in record.parent.iterdir()] == ["server.json"]


class FakeModel(BaseHTTPRequestHandler):
    calls = []
    status = 200
    retry_after = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        FakeModel.calls.append({"path": self.path, "auth": self.headers["Authorization"], "agent": self.headers["User-Agent"], "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
        reply = MODEL_REPLY if FakeModel.status == 200 else b'{"message": "slow down"}'
        self.send_response(FakeModel.status)
        self.send_header("Content-Type", "text/event-stream" if FakeModel.status == 200 else "application/json")
        if FakeModel.retry_after:
            self.send_header("Retry-After", FakeModel.retry_after)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


def serve(drive, key):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(drive, TAILNET_OWNER, {"127.0.0.1"}, key))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def dashboard(monkeypatch):
    model = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "MODEL_URL", f"http://127.0.0.1:{model.server_port}/v1/chat/completions")
    monkeypatch.setattr(FakeModel, "calls", [])
    monkeypatch.setattr(FakeModel, "status", 200)
    monkeypatch.setattr(FakeModel, "retry_after", None)
    drive = Drive({"drive": "d", "board_url": "http://board/p/x"}, None)
    drive.acted = []
    drive.decide = lambda *args: drive.acted.append("decide") or (200, "")
    drive.review_url = lambda number: drive.acted.append("review_url") or "http://review/"
    httpd = serve(drive, MODEL_KEY)
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_port}", drive=drive)
    finally:
        httpd.shutdown()
        model.shutdown()


def whois_answers(monkeypatch, answer):
    asked = []

    def whois(argv, cwd=None):
        asked.append(argv)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(server, "run", whois)
    return asked


def call(url, path, body=None, host=None):
    request = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), headers={**({"Host": host} if host else {}), "Content-Type": "application/json", "Authorization": "Bearer from-the-page"})
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, reply.read(), reply.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def test_the_key_route_is_gone_and_the_page_never_carries_the_key(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, "/ai.json")[0] == 404
    status, page, _ = call(dashboard.url, "/")
    assert status == 200 and '<html class="ai-on"' in page.decode()
    assert MODEL_KEY.encode() not in page


@pytest.mark.parametrize("answer", [json.dumps(TAGGED_WHOIS), json.dumps(OTHER_WHOIS), subprocess.CalledProcessError(1, "tailscale", stderr="no match for IP")])
def test_only_the_owners_device_sees_the_assistant(dashboard, monkeypatch, answer):
    whois_answers(monkeypatch, answer)
    assert "ai-on" not in call(dashboard.url, "/")[1].decode().split("<head>")[0]


def test_a_server_with_no_key_keeps_the_assistant_hidden_from_the_owner_and_has_no_model_route(monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    httpd = serve(Drive({"drive": "d", "board_url": "http://board/p/x"}, None), None)
    url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        assert "ai-on" not in call(url, "/")[1].decode().split("<head>")[0]
        assert call(url, server.MODEL_ROUTE, {"messages": []})[0] == 404
    finally:
        httpd.shutdown()


def test_the_model_key_comes_from_the_keychain_and_its_absence_turns_the_assistant_off(monkeypatch):
    asked = whois_answers(monkeypatch, "secret\n")
    assert server.model_key() == "secret"
    assert asked == [["security", "find-generic-password", "-w", "-s", "cerebras-api-key"]]
    whois_answers(monkeypatch, subprocess.CalledProcessError(44, "security"))
    assert server.model_key() is None


def test_one_model_call_adds_the_key_on_the_server_and_fixes_the_endpoint_and_model(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    asked = {"messages": [{"role": "user", "content": "why is it slow?"}], "stream": True, "tool_choice": "none", "model": "someone-elses-model", "endpoint": "https://elsewhere.example", "seed": 7}
    status, body, headers = call(dashboard.url, server.MODEL_ROUTE, asked)
    assert (status, body, headers.get_content_type()) == (200, MODEL_REPLY, "text/event-stream")
    assert MODEL_KEY.encode() not in body and MODEL_KEY not in str(headers)
    [sent] = FakeModel.calls
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == f"Bearer {MODEL_KEY}" and sent["agent"] == "drive-dashboard"
    assert sent["body"] == {"messages": asked["messages"], "stream": True, "tool_choice": "none", "model": server.MODEL_NAME}


def test_a_refused_model_call_passes_its_status_and_wait_to_the_page_without_the_key(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    monkeypatch.setattr(FakeModel, "status", 429)
    monkeypatch.setattr(FakeModel, "retry_after", "3")
    status, body, headers = call(dashboard.url, server.MODEL_ROUTE, {"messages": []})
    assert (status, headers["Retry-After"]) == (429, "3")
    assert b"slow down" in body and MODEL_KEY.encode() not in body


def test_a_model_that_does_not_answer_comes_back_as_a_bad_gateway_without_the_key(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    monkeypatch.setattr(server, "MODEL_URL", f"http://127.0.0.1:{closed.getsockname()[1]}/v1/chat/completions")
    closed.close()
    status, body, _ = call(dashboard.url, server.MODEL_ROUTE, {"messages": []})
    assert status == 502 and MODEL_KEY.encode() not in body


@pytest.mark.parametrize("answer", REFUSED_CALLERS)
@pytest.mark.parametrize(("path", "body"), [("/api/verdict", VERDICT), ("/api/review/7", {}), (server.MODEL_ROUTE, {"messages": []})])
def test_every_owner_route_refuses_anyone_but_the_owner_and_a_failed_lookup(dashboard, monkeypatch, path, body, answer):
    whois_answers(monkeypatch, answer)
    status, reply, _ = call(dashboard.url, path, body)
    assert status == 403 and MODEL_KEY.encode() not in reply
    assert dashboard.drive.acted == [] and FakeModel.calls == []


def test_a_verdict_from_the_owner_lands_and_the_lookup_names_the_callers_address(dashboard, monkeypatch):
    asked = whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, "/api/verdict", VERDICT)[:2] == (200, b"{}")
    assert dashboard.drive.acted == ["decide"]
    assert asked == [["tailscale", "whois", "--json", "127.0.0.1"]]


def test_the_owner_still_opens_a_review(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    status, body, _ = call(dashboard.url, "/api/review/7", {})
    assert status == 200 and json.loads(body)["url"] == "http://review/"
    assert dashboard.drive.acted == ["review_url"]


def test_a_link_or_image_on_another_site_cannot_start_a_review(dashboard, monkeypatch):
    asked = whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, "/api/review/7")[0] == 404
    assert dashboard.drive.acted == []
    assert asked == []


@pytest.mark.parametrize(("path", "body"), [("/api/verdict", VERDICT), ("/api/review/7", {}), (server.MODEL_ROUTE, {"messages": []})])
def test_a_rebinding_page_is_refused_though_the_owner_s_device_carries_it(dashboard, monkeypatch, path, body):
    asked = whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, path, body, host="evil.example:61210")[0] == 403
    assert dashboard.drive.acted == [] and FakeModel.calls == []
    assert asked == []


def test_the_names_the_owner_may_use_are_this_macs_own(monkeypatch):
    status = {"Self": {"DNSName": "Studio.tail.ts.net.", "TailscaleIPs": ["100.1.2.3", "fd7a::1"]}}
    monkeypatch.setattr(server, "run", lambda argv, cwd=None: json.dumps(status))
    assert server.own_names("100.1.2.3") == {"studio.tail.ts.net", "studio", "100.1.2.3", "fd7a::1"}


def test_the_review_assistant_never_runs_with_permissions_bypassed(tmp_path, monkeypatch):
    class Stop(Exception):
        pass

    def run(argv, **_):
        ran.append(argv)
        raise Stop

    ran = []
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("subprocess.run", run)
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"cc-review@cc-review": [{"installPath": "/cc-review/0.46.1"}]}}))
    assistant = ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, "http://model")
    assistant.queues[7] = queue.Queue()
    assistant.scope(7).mkdir(parents=True)
    assistant.queues[7].put({"type": "ai.request.created", "request": {"id": "1", "source": "system"}})
    monkeypatch.setattr(assistant, "board_row", lambda number: [{"number": number, "head": "abc"}])
    with pytest.raises(Stop):
        assistant.serve(7)
    argv = ran[0]
    assert "bypassPermissions" not in argv and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert "--restricted" in argv and argv[argv.index("--plugin-dir") + 1] == "/cc-review/0.46.1"
    assert argv[argv.index("--tools") + 1] == "Agent,Bash,Read,Grep,Glob,ToolSearch" and argv[argv.index("--add-dir") + 1].endswith("/.cc-review")
    assert set(assistant.allowed_tools(7)) <= set(argv)
    assert not {"Bash", "Read", "Write", "Edit"} & set(argv)
    assert "curl" not in argv[2] and '"head": "abc"' in argv[2]


def test_the_review_assistant_reads_its_row_and_runs_on_without_one(tmp_path):
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"board": {"rows": [{"number": 7, "head": "abc"}, {"number": 8, "head": "def"}]}}))
    assert ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, model.as_uri()).board_row(7) == [{"number": 7, "head": "abc"}]
    assert ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, (tmp_path / "gone.json").as_uri()).board_row(7) == []


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class CciStub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if not self.path.startswith("/v1/stream"):
            body = json.dumps(self.server.records).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for record in self.server.records:
            event = f"data: {json.dumps(record)}\n\n".encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(event), event))
        self.wfile.flush()
        self.server.streams.put(self.connection)
        self.server.cut.wait()


def cci_stub(port, records):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), CciStub)
    httpd.records, httpd.streams, httpd.cut = records, queue.Queue(), threading.Event()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def stop_stub(httpd):
    httpd.cut.set()
    httpd.shutdown()
    httpd.server_close()


def test_a_refresh_waits_out_a_daemon_restart_and_says_when_it_is_back(monkeypatch, capsys):
    port = free_port()
    monkeypatch.setattr(server, "CCI", f"http://127.0.0.1:{port}/v1")
    started = []
    real_sleep = time.sleep

    def sleep(seconds):
        if not started:
            started.append(cci_stub(port, [{"lane": "desk"}]))
        real_sleep(seconds)

    monkeypatch.setattr(server.time, "sleep", sleep)
    try:
        assert server.cci("lanes", drive="d") == [{"lane": "desk"}]
    finally:
        for httpd in started:
            stop_stub(httpd)
    assert capsys.readouterr().out.count("the cci daemon is back after") == 1


def test_the_record_stream_follows_the_daemon_back_after_a_restart_cuts_it(monkeypatch, capsys):
    port = free_port()
    monkeypatch.setattr(server, "CCI", f"http://127.0.0.1:{port}/v1")
    drive = Drive({"drive": "d"}, None)
    first = cci_stub(port, [{"seq": 1, "kind": "note"}])
    threading.Thread(target=drive.follow_records, args=(0,), daemon=True).start()
    first.streams.get(timeout=10).shutdown(socket.SHUT_RDWR)
    stop_stub(first)
    second = cci_stub(port, [{"seq": 2, "kind": "note"}])
    second.streams.get(timeout=10)
    deadline = time.monotonic() + 10
    while [record["seq"] for record in list(drive.records)] != [1, 2]:
        assert time.monotonic() < deadline, list(drive.records)
        time.sleep(0.05)
    assert "the cci record stream is back after" in capsys.readouterr().out
    assert "records" not in drive.errors
