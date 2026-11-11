import json
import subprocess
from collections.abc import Callable

import pytest
from drive import card, cli, limits, reads, state, tools
from drive.state import DriveError

THREAD = "1794200000.000100"
ANSWER = {
    "id": "73060f0349f75a509980c17434a2ef862397394c",
    "title": "Owner 9:54 PM Nov 10: should drive verbs such as drive heard be MCP tool calls?",
    "body": '"do a pass on making yhouir tools fit youir usage paterns and efficient"\nNotes: cci owner 660781',
}


def test_a_call_runs_its_ops_in_order_and_an_op_after_an_add_acts_on_what_it_made(demo: str) -> None:
    order = {"words": "Fix the card", "from": f"slack:D123/{THREAD}", "thread": THREAD, "project": "The drive", "workstream": "Drive tooling"}
    said = tools.run("job", demo, {"ops": [{"add": {"kind": "builder", "order": order, "goal": "fix it"}}, {"set": {"branch": "lr-fix", "said": "pushed"}}]})
    assert said.splitlines()[:2] == ["1 order add: o1", "1 job add: j1"]
    assert said.splitlines()[2].startswith("2 job set: j1")
    job = state.load(demo)["jobs"]["j1"]
    assert (job["order"], job["branch"], job["said"]) == ("o1", "lr-fix", "pushed")
    status = tools.run("status", demo, {"ids": ["o1", "j1"]}).splitlines()
    assert len(status) > 2 and not status[0].startswith("1 status")


def test_a_refusal_keeps_the_lines_before_it_and_names_the_ops_left_unrun(demo: str) -> None:
    with pytest.raises(DriveError) as refused:
        tools.run("order", demo, {"ops": [{"add": {"words": "Ship it", "project": "The drive", "workstream": "Drive tooling"}}, {"set": {"id": "o9", "note": "x"}}, {"set": {"note": "never"}}]})
    assert str(refused.value).splitlines() == ["1 order add: o1", "2 order set refused: drive: no order o9; ops 3-3 not run"]
    assert state.load(demo)["orders"]["o1"]["note"] is None
    with pytest.raises(DriveError, match="invalid choice: 'finished'"):
        tools.run("order", demo, {"ops": [{"set": {"id": "o1", "state": "finished"}}]})
    for empty in ({"ops": []}, {}):
        with pytest.raises(DriveError, match="name ops: a list of at least one of add, set, posted"):
            tools.run("order", demo, empty)
    with pytest.raises(DriveError, match="the job tool runs add, set, brief, not launch"):
        tools.run("job", demo, {"ops": [{"launch": {"id": "j1"}}]})
    with pytest.raises(DriveError, match="drive order set takes no colour"):
        tools.run("order", demo, {"ops": [{"set": {"id": "o1", "colour": "red"}}]})


def test_a_dry_run_prints_each_command_and_changes_nothing(demo: str) -> None:
    said = tools.run("pr", demo, {"dry_run": True, "ops": [{"set": {"url": "https://github.com/a/b/pull/1", "tap": False, "why": "superseded", "waits": ["c1", "c2"]}}]})
    assert said == "1 drive pr set https://github.com/a/b/pull/1 --waits=c1 --waits=c2 --no-tap --why=superseded"
    assert tools.run("status", demo, {"dry_run": True, "ids": ["o1", "j2"]}) == "1 drive status o1 j2"
    assert state.load(demo)["prs"] == {}


def test_each_verb_tool_is_typed_from_the_parser_the_cli_runs() -> None:
    listed = {tool["name"]: tool for tool in tools.verb_tools()}
    assert ["say", *listed] == list(cli.MCP_TOOLS)
    ops = {next(iter(shape["properties"])): shape["properties"] for shape in listed["job"]["inputSchema"]["properties"]["ops"]["items"]["oneOf"]}
    job_set = ops["set"]["set"]
    assert job_set["properties"]["state"]["enum"] == list(state.JOB_STATES)
    assert (job_set["properties"]["waits"]["type"], "id" in job_set["required"]) == ("array", False)
    assert ops["add"]["add"]["properties"]["order"]["anyOf"][1]["required"] == ["words", "project", "workstream"]
    assert listed["status"]["annotations"] == {"readOnlyHint": True}


def test_a_record_goes_to_the_other_session_and_a_tap_takes_its_ask_and_job_from_the_waits_id(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[list[str], list[dict]]] = []
    ok = subprocess.CompletedProcess([], 0, "#41\n#42\n", "")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append((argv, json.loads(kwargs["input"]))) or ok)
    records = [{"kind": "answer", "text": "Ring", "waits": "demo:j2-7", "undone": True}, {"kind": "defect", "text": "cc-slack delete hides --url"}]
    assert tools.run("record", demo, {"records": records}) == "1 answer #41\n2 defect #42"
    assert posted == [
        (
            ["cci", "post", "--drive", "demo", "--lane", "root", "--batch", "-"],
            [
                {"kind": "answer", "to": ["communicator@demo"], "re": ["7"], "text": "undone: Ring", "attr": {"job": "j2", "undone": "Ring"}},
                {"kind": "defect", "to": ["communicator@demo"], "text": "cc-slack delete hides --url"},
            ],
        )
    ]
    with pytest.raises(DriveError, match="a ready goes through cci post"):
        tools.run("record", demo, {"records": [{"kind": "ready", "text": "Land #4"}]})
    with pytest.raises(DriveError, match="a card of drive other, not demo"):
        tools.run("record", demo, {"records": [{"kind": "answer", "text": "Ring", "waits": "other:j2-7"}]})
    with pytest.raises(DriveError, match="name records"):
        tools.run("record", demo, {"records": []})
    with pytest.raises(DriveError, match="no question card's waits id"):
        tools.run("record", demo, {"records": [{"kind": "answer", "text": "Approve", "waits": "merge:Forge-AI:monorepo:42"}]})


def test_a_long_defect_goes_to_the_other_session_as_one_line_with_its_whole_text_atop_its_body(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[list[dict]] = []
    ok = subprocess.CompletedProcess([], 0, "#43\n", "")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append(json.loads(kwargs["input"])) or ok)
    defect = "cc-slack delete hides --url: " + "the card stays up after its delete and the dashboard keeps it, " * 6
    assert len(defect) > 400
    assert tools.run("record", demo, {"records": [{"kind": "defect", "text": defect, "body": "seen on the run page"}]}) == "1 defect #43"
    [[sent]] = posted
    assert len(sent["text"]) <= limits.RECORD_CHARS and sent["text"].startswith("cc-slack delete hides --url:") and sent["text"].endswith("…")
    assert sent["body"] == f"{defect.strip()}\n\nseen on the run page"


def test_a_sub_coordinator_posts_from_its_lane_and_his_answer_to_its_card_or_an_owner_record_heard_routes_reaches_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    posted: list[tuple[str, list[dict]]] = []

    def post(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        records = json.loads(kwargs["input"])
        posted.append((argv[argv.index("--lane") + 1], records))
        return subprocess.CompletedProcess(argv, 0, "".join(f"#{n}\n" for n, _ in enumerate(records)), "")

    monkeypatch.setattr(subprocess, "run", post)
    tap = {"kind": "answer", "text": "Ring", "waits": "demo:j1-7"}
    tools.run("record", demo, {"records": [tap, {"kind": "owner", "text": "also the logs page", "to": "iris"}, {"kind": "owner", "text": "a new order"}]})
    card.question_answered({"waits": "demo:j1-8", "label": "Ring", "url": "https://acme.slack.com/archives/D123/p1794200000000100"})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    tools.run("record", demo, {"records": [{"kind": "defect", "text": "cc-slack delete hides --url"}]})
    assert [(lane, [(record["kind"], record["to"]) for record in records]) for lane, records in posted] == [
        ("communicator", [("answer", ["iris@demo"]), ("owner", ["iris@demo"]), ("owner", ["root@demo"])]),
        ("communicator", [("answer", ["iris@demo"])]),
        ("iris", [("defect", ["communicator@demo"])]),
    ]
    with pytest.raises(DriveError, match="to platform: no session of drive demo reads that lane; name one of root, communicator, iris"):
        tools.run("record", demo, {"records": [{"kind": "owner", "text": "fix the alert", "to": "platform"}]})


def test_a_brief_carries_his_order_and_each_ruling_word_for_word(demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: json.dumps(ANSWER))
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "do a pass on the tools", "--from", f"slack:D123/{THREAD}")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    run("job", "add", "builder", "--order", "o1", "--goal", "make the verbs MCP tools")
    brief = {"id": "j2", "serves": "drive tooling", "done_when": "installed", "where": "yasyf/cc-skills main", "ruling": ["73060f0"]}
    said = tools.run("job", demo, {"ops": [{"brief": brief}]})
    path = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    assert said == f"1 job brief: Read your brief first: {path}; his words from o1, rulings: 1, earlier jobs: 0"
    written = path.read_text()
    assert written.startswith("Order (his words): do a pass on the tools\nServes: drive tooling\nGoal: make the verbs MCP tools\n")
    assert f"Decisions that bind:\n- {ANSWER['title']} (73060f0)\n  {ANSWER['body'].splitlines()[0]}\n  Notes: cci owner 660781\n" in written
    assert written.endswith("Drive: demo, order o1, job j2\n")
    assert state.load(demo)["jobs"]["j2"]["brief"] == str(path)
