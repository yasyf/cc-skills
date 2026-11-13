import contextlib
import io
import json
import os
import subprocess
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from drive import card, cli, limits, mcp, reads, settled, slack, state, superseded, tools, view, watch
from conftest import RESEARCH, Answers, Heard, Withdrawals
from drive.state import DriveError

THREAD = "1794200000.000100"
CARD = "https://acme.slack.com/archives/D123/p1794200000000700"
UNDER_CARD = f"{CARD}?thread_ts=1794200000.000700"
PR_CARD = "https://acme.slack.com/archives/D123/p1794200000000900"
TYPED = "things liek this should be egtting almost immediate reponses but they are not"
UNSAID = settled.unsaid
ANSWER = {
    "id": "73060f0349f75a509980c17434a2ef862397394c",
    "tags": [],
    "title": "Owner 9:54 PM Nov 10: should drive verbs such as drive heard be MCP tool calls?",
    "body": '"do a pass on making yhouir tools fit youir usage paterns and efficient"\nNotes: cci owner 660781',
}


def test_a_call_runs_its_ops_in_order_and_an_op_after_an_add_acts_on_what_it_made(demo: str) -> None:
    order = {"words": "Fix the card", "title": "Fix the card", "from": f"slack:D123/{THREAD}", "thread": THREAD, "project": "The drive", "workstream": "Drive tooling"}
    said = tools.run("job", demo, {"ops": [{"add": {"kind": "builder", "order": order, "goal": "fix it"}}, {"set": {"branch": "lr-fix", "said": "pushed"}}]})
    assert said.splitlines()[:2] == ["1 order add: o1", "1 job add: j1"]
    assert said.splitlines()[2].startswith("2 job set: j1")
    job = state.load(demo)["jobs"]["j1"]
    assert (job["order"], job["branch"], job["said"]) == ("o1", "lr-fix", "pushed")
    status = tools.run("status", demo, {"ids": ["o1", "j1"]}).splitlines()
    assert len(status) > 2 and not status[0].startswith("1 status")


def test_a_call_with_a_refused_op_writes_nothing_and_names_every_refused_op_with_its_fix(demo: str) -> None:
    with pytest.raises(DriveError) as refused:
        tools.run("order", demo, {"ops": [{"add": {"words": "Ship it", "title": "Short title", "project": "The drive", "workstream": "Drive tooling"}}, {"set": {"id": "o9", "note": "x"}}, {"set": {"note": "never"}}]})
    assert str(refused.value).splitlines() == [
        "refused, nothing written: 1 of 3 ops refused; fix each and send the whole call again",
        "2 order set refused: no order o9",
    ]
    assert state.load(demo)["orders"] == {}
    with pytest.raises(DriveError, match="invalid choice: 'finished'"):
        tools.run("order", demo, {"ops": [{"set": {"id": "o1", "state": "finished"}}]})
    for empty in ({"ops": []}, {}):
        with pytest.raises(DriveError, match="name ops: a list of at least one of add, set, posted"):
            tools.run("order", demo, empty)
    with pytest.raises(DriveError, match="the job tool runs add, set, brief, not launch"):
        tools.run("job", demo, {"ops": [{"launch": {"id": "j1"}}]})
    with pytest.raises(DriveError, match="drive order set takes no colour"):
        tools.run("order", demo, {"ops": [{"set": {"id": "o1", "colour": "red"}}]})
    with pytest.raises(DriveError, match="1 order set refused: name its id, since no add came before it"):
        tools.run("order", demo, {"ops": [{"set": {"note": "x"}}]})


def test_a_call_whose_second_tooling_order_check_post_refuses_adds_neither_and_names_that_op_with_its_finding(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(slack, "check_post", lambda text, asked: ["1:44 drive-term 'o610': say what happens to the reader's work instead"] if "o610" in text else [])
    ops = [{"add": {"words": "Drive tooling: a report states job start times in the future", "title": "Short title"}}, {"add": {"words": "Drive tooling: a promised time left o610 due", "title": "Short title"}}]
    with pytest.raises(DriveError) as refused:
        tools.run("order", demo, {"ops": ops})
    [head, line] = str(refused.value).splitlines()
    assert head == "refused, nothing written: 1 of 2 ops refused; fix each and send the whole call again"
    assert line.startswith("2 order add \"Drive tooling: a promised time left o610 due\" refused: refused, `cc-slack check-post` finds 1:44 drive-term 'o610'")
    assert state.load(demo)["orders"] == {}
    with pytest.raises(DriveError, match="2 order add .* refused: refused, `cc-slack check-post` finds"):
        tools.run("order", demo, {"dry_run": True, "ops": ops})
    ops[1]["add"]["words"] = "Drive tooling: a promised time stays due until a handler sets it"
    assert tools.run("order", demo, {"ops": ops}).splitlines() == ["1 order add: o1", "2 order add: o2"]
    assert [(order["project"], order["workstream"]) for order in state.load(demo)["orders"].values()] == [("The drive", "Drive tooling")] * 2


def test_a_job_add_naming_no_part_of_the_two_part_order_it_adds_is_refused_before_that_order_is_written(demo: str) -> None:
    order = {"words": "Drive tooling: a refused write leaves nothing half-written", "title": "Refused writes leave nothing", "kind": "build", "parts": ["all or none", "default filing"]}
    with pytest.raises(DriveError) as refused:
        tools.run("job", demo, {"ops": [{"add": {"kind": "reader", "order": order, "goal": "research it"}}, {"brief": {"serves": "drive tooling", "done_when": "x", "where": "y"}}]})
    assert str(refused.value).splitlines()[1] == (
        '1 job add "research it" refused: o1 has parts "all or none", "default filing"; name the one this covers with --part'
    )
    assert state.load(demo)["orders"] == {}


def test_a_call_with_three_refused_ops_prints_all_three(demo: str) -> None:
    tools.run("order", demo, {"ops": [{"add": {"words": "Ship it", "title": "Short title", "project": "Iris", "workstream": "Run page"}}]})
    with pytest.raises(DriveError) as refused:
        tools.run("order", demo, {"ops": [{"add": {"words": "ship the run page", "title": "Short title"}}, {"set": {"id": "o1", "note": "x"}}, {"set": {"id": "o7"}}, {"told": {"id": "o8", "link": "x"}}]})
    assert str(refused.value).splitlines() == [
        "refused, nothing written: 3 of 4 ops refused; fix each and send the whole call again",
        '1 order add "ship the run page" refused: refused, no order added: name --project and --workstream, which file his order on the'
        " dashboard's Now screen, reusing a pair in use where it fits: Iris / Run page",
        "3 order set refused: no order o7",
        "4 order told refused: no order o8",
    ]
    assert state.load(demo)["orders"]["o1"]["note"] is None


def test_a_refusal_while_a_job_call_runs_writes_nothing_and_a_passing_call_saves_the_drive_once(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    order = {"words": "Fix the card", "title": "Fix the card", "project": "The drive", "workstream": "Drive tooling"}
    lands = {"kind": "reader", "order": "o1", "goal": "read", "lands": ["https://github.com/a/b/pull/1"]}
    with pytest.raises(DriveError) as refused:
        tools.run("job", demo, {"ops": [{"add": {"kind": "builder", "order": order, "goal": "fix it"}}, {"add": lands}]})
    assert str(refused.value) == "2 job add refused: drive: --lands names the PRs a builder lands, which only a builder does; nothing written: fix it and send the whole call again"
    assert (state.load(demo)["orders"], state.load(demo)["jobs"]) == ({}, {})
    saves: list[str] = []
    save = state.save
    monkeypatch.setattr(state, "save", lambda name, data: saves.append(name) or save(name, data))
    tools.run("job", demo, {"ops": [{"add": {"kind": "builder", "order": order, "goal": "fix it"}}, {"set": {"branch": "lr-fix"}}, {"set": {"said": "pushed"}}]})
    assert saves == [demo]
    assert state.load(demo)["jobs"]["j1"]["branch"] == "lr-fix"


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
    assert ops["add"]["add"]["properties"]["order"]["anyOf"][1]["required"] == ["words", "title"]
    assert listed["status"]["annotations"] == {"readOnlyHint": True}


PLAN = {
    "id": "ec8bf71cc2820ecf8c5bb8b2695825c2cf3e7c54",
    "title": "One copy per store",
    "status": "approved",
    "body": (
        "# One copy per store\n\n## Context\nThe copy runs once a night.\n\n## Approach\n### Step 1: the bucket\nWrite each copy to"
        " the bucket.\n```sh\n# a comment, not a heading\n```\n### Step 2: the catch-up\nThe catch-up runs on start.\n"
    ),
}


def test_drive_break_glass_grants_in_the_jobs_name_holding_no_install_lock_then_runs_its_command_in_the_grants_environment_and_exits_as_it_does(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "count the stranded table copies")
    run("job", "add", "reader", "--order", "o1", "--goal", "count them")
    granted, ran = [], []
    grant = reads.Grant("https://buildkite.com/forge/break-glass/builds/1926", "plat-prod", state.parse_time("2026-11-12T12:22:25Z"), {"AWS_PROFILE": "plat-prod"}, reads.Stops(-1, []))

    @contextlib.contextmanager
    def held(stack: str, reason: str, asker: str, checkout: object) -> Iterator[reads.Grant]:
        granted.append((stack, reason, asker, str(checkout), dict(state.HELD_INSTALL)))
        yield grant

    monkeypatch.setattr(reads, "break_glass", held)
    monkeypatch.setattr(reads, "run_granted", lambda command, granted: ran.append((command, granted.env)) or 3)
    state.hold_install()
    assert run("break-glass", "j1", "--stack", "sandsql/plat-usw2-prod", "--reason", "count rows, read only", "--", "aws", "sts", "get-caller-identity")[0] == 3
    coordinator = state.load(demo)["roles"]["coordinator"]["cwd"]
    assert granted == [("sandsql/plat-usw2-prod", "count rows, read only", "demo j1", coordinator, {})]
    assert ran == [(["aws", "sts", "get-caller-identity"], grant.env)]
    assert run("break-glass", "j9", "--stack", "sandsql/plat-usw2-prod", "--reason", "x", "--", "aws", "s3", "ls")[0] == 2

    def failed(*args: object) -> Iterator[reads.Grant]:
        raise reads.ReadError(f"the break-glass grant {grant.link} failed, so nothing ran")

    monkeypatch.setattr(reads, "break_glass", failed)
    assert run("break-glass", "o1", "--stack", "sandsql/plat-usw2-prod", "--reason", "x", "--", "aws", "s3", "ls")[0] == 2
    assert len(granted) == len(ran) == 1


@pytest.mark.parametrize(
    ("words", "why"),
    [
        (
            ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "count"],
            "drive order add: refused, nothing ran: a lane runs only these verbs: job report, job ask, job set, job replies, job held, pr add, plan search, manual, status",
        ),
        (
            ["job", "report", "j8", "-"],
            "drive job report: refused, nothing ran: it names j8, and a lane's drive writes act on its own job, j7, alone, while its reads read any job",
        ),
        (["job", "report", "--", "j8", "-"], "drive job report: refused, nothing ran: it names j8"),
        (["job", "ask", "j8", "ask.md"], "drive job ask: refused, nothing ran: it names j8"),
        (["job", "set", "j8", "--repro", "proof.sh"], "drive job set: refused, nothing ran: it names j8"),
        (["pr", "add", "https://github.com/yasyf/cc-skills/pull/1", "--jo=j8"], "drive pr add: refused, nothing ran: it names j8"),
        (
            ["break-glass", "j7", "--stack", "sandsql/plat-usw2-prod", "--reason", "x", "--", "aws", "s3", "ls"],
            f"drive break-glass: refused, nothing ran: no Sprite lane runs break-glass, by {reads.NO_SPRITE_GRANT}: return BLOCKED naming the step and its command",
        ),
        (["job", "set", "j7", "--sta", "done"], "drive job set: refused, nothing ran: a lane's job set takes only --repro, and it names --state"),
        (["status", "--drive", "other"], "drive status: refused, nothing ran: it names drive other, and a lane acts on its own drive, demo, alone"),
        (["status", "--dri", "other"], "drive status: refused, nothing ran: it names drive other"),
        (["job", "replies", "j7", "--drive=other"], "drive job replies: refused, nothing ran: it names drive other"),
    ],
)
def test_a_sprite_lanes_verb_acting_on_another_job_or_drive_however_it_is_spelled_runs_nothing(
    words: list[str], why: str, demo: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(reads.SPRITE_LANE, "1")
    monkeypatch.setenv(state.OWN_JOB_ENV, "j7")
    monkeypatch.setenv("LONG_RUNNING_DRIVE", demo)
    monkeypatch.setattr(reads, "break_glass", lambda *args: pytest.fail("a refused break-glass started a grant"))
    capsys.readouterr()
    assert cli.main(words) == 2
    assert capsys.readouterr().err.startswith(why)


def test_a_sprite_lane_reads_another_job_as_a_mac_lane_does_its_bare_status_reads_its_own_job_and_nothing_it_starts_carries_its_mark(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Copies", "--project", "The drive", "--workstream", "Drive tooling", "count the stranded table copies")
    run("order", "add", "--title", "Cards", "--project", "The drive", "--workstream", "Drive tooling", "sweep the stale cards")
    run("job", "add", "reader", "--order", "o1", "--goal", "count them")
    run("job", "add", "builder", "--order", "o2", "--goal", "sweep them")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    on_mac = {words: run(*words) for words in (("job", "report", "j1"), ("job", "replies", "j1"), ("status", "j1"), ("status", "j2"), ("status",))}
    monkeypatch.setenv(state.OWN_JOB_ENV, "j2")
    monkeypatch.setenv("LONG_RUNNING_DRIVE", demo)

    def lane(*words: str) -> tuple[int, str]:
        monkeypatch.setenv(reads.SPRITE_LANE, "1")
        return run(*words)

    for words in (("job", "report", "j1"), ("job", "replies", "j1"), ("status", "j1")):
        assert lane(*words) == on_mac[words]
    assert on_mac[("job", "report", "j1")][1].startswith(RESEARCH.strip().splitlines()[0])
    assert lane("status") == on_mac[("status", "j2")] != on_mac[("status",)]
    assert reads.SPRITE_LANE not in os.environ


def test_a_sprite_lanes_kept_report_answers_that_it_was_kept_with_its_first_line_never_a_path_on_this_mac(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Copies", "--project", "The drive", "--workstream", "Drive tooling", "count the stranded table copies")
    run("job", "add", "reader", "--order", "o1", "--goal", "count them")
    monkeypatch.setenv(reads.SPRITE_LANE, "1")
    monkeypatch.setenv(state.OWN_JOB_ENV, "j1")
    monkeypatch.setenv("LONG_RUNNING_DRIVE", demo)
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    assert run("job", "report", "j1", "-") == (0, "kept j1's report: DONE researched the store copy")


def test_drive_break_glass_refuses_a_blank_reason_before_any_grant(demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "count the stranded table copies")
    monkeypatch.setattr(reads, "break_glass", lambda *args: pytest.fail("a blank reason started a grant"))
    with pytest.raises(SystemExit) as refused:
        run("break-glass", "o1", "--stack", "sandsql/plat-usw2-prod", "--reason", " ", "--", "aws", "s3", "ls")
    assert refused.value.code == 2


def test_search_plan_returns_the_plan_lines_carrying_its_words_under_their_headings(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str]] = []

    def ccn(argv: list[str], checkout: object) -> str:
        calls.append((argv, str(checkout)))
        return json.dumps([{key: PLAN[key] for key in ("id", "title", "status")}] if argv[2] == "search" else PLAN)

    monkeypatch.setattr(reads, "run", ccn)
    said = tools.run("search_plan", demo, {"words": ["catch-up", "start"]})
    assert said.splitlines() == ["== One copy per store (plan ec8bf71, approved) › One copy per store › Approach › Step 2: the catch-up", "The catch-up runs on start."]
    coordinator = state.load(demo)["roles"]["coordinator"]["cwd"]
    assert calls[:2] == [
        (["ccn", "plan", "search", "catch-up start", "--limit", "5", "--json"], coordinator),
        (["ccn", "plan", "show", PLAN["id"], "--json"], coordinator),
    ]
    assert run("plan", "search", "bucket") == (
        0,
        "== One copy per store (plan ec8bf71, approved) › One copy per store › Approach › Step 1: the bucket\nWrite each copy to the bucket.",
    )
    assert run("plan", "search", "deploy", "freeze") == (0, 'no plan the coordinator keeps in cc-notes has a line on "deploy", "freeze"')
    listed = {tool["name"]: tool for tool in tools.verb_tools()}["search_plan"]
    assert (listed["inputSchema"]["required"], listed["annotations"]) == (["words"], {"readOnlyHint": True})


def test_search_plan_answers_no_line_for_phrases_its_plans_carry_only_split_and_cuts_its_answer_at_the_read_budget(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def plans(body: str) -> None:
        plan = {**PLAN, "body": body}
        monkeypatch.setattr(reads, "run", lambda argv, checkout: json.dumps([plan] if argv[2] == "search" else plan))

    plans("# Installs\nThe older installs wait for the lock; none of the verbs is done by one operator.\n")
    said = tools.run("search_plan", demo, {"words": ["install lock", "older install", "older_verbs_done", "one install per verb"]})
    assert said == 'no plan the coordinator keeps in cc-notes has a line on "install lock", "older install", "older_verbs_done", "one install per verb"'
    plans("# Installs\n" + "Each verb takes the install lock.\n" * 1000)
    said = run("plan", "search", "install lock")[1]
    assert said.splitlines()[:3] == [f"[first {reads.BUDGET} of 34057 characters]", "== One copy per store (plan ec8bf71, approved) › Installs", "Each verb takes the install lock."]
    assert len(said) == len(f"[first {reads.BUDGET} of 34057 characters]\n") + reads.BUDGET


def test_a_tool_call_naming_its_drive_acts_on_that_drive_from_a_session_no_drive_binds(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools.run("order", demo, {"ops": [{"add": {"words": "Fix the card", "title": "Short title", "project": "The drive", "workstream": "Drive tooling"}}]})
    run("start", "other", "--role", "coordinator")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "bound-to-none")

    def call(arguments: dict) -> tuple[int, str]:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(arguments)))
        code = cli.main(["tool", "status"])
        printed = capsys.readouterr()
        return code, printed.out + printed.err

    assert call({}) == (2, "drive: several drives, pass --drive: demo, other\n")
    assert "Fix the card" in call({"drive": demo})[1]
    assert "Fix the card" not in call({"drive": "other"})[1]
    assert all(tool["inputSchema"]["properties"]["drive"] == tools.DRIVE for tool in mcp.tool_list())


def test_tool_help_prints_each_field_of_its_input_with_its_type_whether_it_is_required_and_its_description(
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = next(tool for tool in tools.verb_tools() if tool["name"] == "record")
    assert cli.main(["tool", "record", "--help"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"record: {record['description']}"
    assert "records (list of object, required)" in lines
    assert f"records[].kind (owner | answer | handoff | defect, required): {tools.RECORD['properties']['kind']['description']}" in lines
    assert f"records[].waits (string): {tools.RECORD['properties']['waits']['description']}" in lines
    assert f"dry_run (boolean): {tools.DRY_RUN['description']}" in lines
    assert cli.main(["tool", "job", "-h"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "ops[].add (object)" in lines
    goal, words = tools.schema(tools.leaf("job", "add"))["properties"]["goal"], tools.schema(tools.leaf("order", "add"))["properties"]["words"]
    assert f"ops[].add.goal (string, required): {goal['description']}" in lines
    assert f"ops[].add.order.words (string, required): {words['description']}" in lines


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
                {"kind": "defect", "to": ["meta@demo"], "text": "cc-slack delete hides --url"},
            ],
        )
    ]
    with pytest.raises(DriveError, match="a report goes through the cci `post` tool"):
        tools.run("record", demo, {"records": [{"kind": "report", "text": "Land #4"}]})
    with pytest.raises(DriveError, match="a card of drive other, not demo"):
        tools.run("record", demo, {"records": [{"kind": "answer", "text": "Ring", "waits": "other:j2-7"}]})
    with pytest.raises(DriveError, match="name records"):
        tools.run("record", demo, {"records": []})
    with pytest.raises(DriveError, match="no question card's waits id"):
        tools.run("record", demo, {"records": [{"kind": "answer", "text": "Approve", "waits": "merge:Forge-AI:monorepo:42"}]})


def test_a_record_names_the_one_seq_it_answers_and_one_naming_several_is_refused_before_any_record_posts(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[list[dict]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append(json.loads(kwargs["input"])) or subprocess.CompletedProcess([], 0, "#44\n", ""))
    defect = {"kind": "defect", "text": "drive record crashes after posting"}
    with pytest.raises(DriveError, match="re names one seq, not 2: cci stores one record per seq"):
        tools.run("record", demo, {"records": [defect, defect | {"re": ["672196", "672234"]}]})
    assert posted == []
    assert tools.run("record", demo, {"records": [defect | {"re": "672234"}]}) == "1 defect #44"
    assert [[record["re"] for record in records] for records in posted] == [[["672234"]]]


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


def test_a_defect_quoting_his_messages_clock_time_or_a_builds_start_posts_since_it_goes_between_sessions(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[list[dict]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append(json.loads(kwargs["input"])) or subprocess.CompletedProcess([], 0, "#45\n", ""))
    defects = (
        "record refused his 3:40 PM pick beside https://acme.slack.com/archives/D123/p1794526841094869, nothing posted",
        "say {started:j1}: two posts say the SandSQL release started 9:52 AM, but build 15020 started 11:30 AM; the job's start was printed as the release's.",
    )
    for defect in defects:
        assert tools.run("record", demo, {"records": [{"kind": "defect", "text": defect}]}) == "1 defect #45", defect
    assert [sent["text"] for [sent] in posted] == [limits.first_line(defect) for defect in defects]


def test_a_sub_coordinator_posts_from_its_lane_and_his_answer_to_its_card_or_an_owner_record_heard_routes_reaches_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
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
    open_cards(monkeypatch, "demo:j1-7")
    tap = {"kind": "answer", "text": "Ring", "waits": "demo:j1-7"}
    owners = [{"kind": "owner", "text": "also the logs page", "to": "iris", "about": "his work"}, {"kind": "owner", "text": "a new order", "about": "his work"}]
    tools.run("record", demo, {"records": [tap, *owners]})
    card.question_answered({"waits": "demo:j1-8", "label": "Ring", "url": "https://acme.slack.com/archives/D123/p1794200000000100"})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    tools.run("record", demo, {"records": [{"kind": "defect", "text": "cc-slack delete hides --url"}]})
    assert [(lane, [(record["kind"], record["to"]) for record in records]) for lane, records in posted] == [
        ("communicator", [("answer", ["iris@demo"]), ("owner", ["iris@demo"]), ("owner", ["root@demo"])]),
        ("communicator", [("answer", ["iris@demo"])]),
        ("iris", [("defect", ["meta@demo"])]),
    ]
    with pytest.raises(DriveError, match="to platform: no session of drive demo reads that lane; name one of root, communicator, meta, iris"):
        tools.run("record", demo, {"records": [{"kind": "owner", "text": "fix the alert", "to": "platform", "about": "his work"}]})


def test_the_communicators_records_about_the_drive_reach_the_meta_session_alone_and_each_owner_record_names_what_it_is_about(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, list[dict]]] = []

    def post(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        records = json.loads(kwargs["input"])
        posted.append((argv[argv.index("--lane") + 1], records))
        return subprocess.CompletedProcess(argv, 0, "".join(f"#{n}\n" for n, _ in enumerate(records)), "")

    tooling = "1794200000.000300"
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page", "--thread", THREAD)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: cards say why", "--thread", tooling)
    monkeypatch.setattr(subprocess, "run", post)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    defect = {"kind": "defect", "text": "cc-slack delete hides --url"}
    rewrite = {"words": "Drive tooling: meta stuff goes to a meta root", "title": "Meta stuff to a meta root"}
    ruling = {"kind": "owner", "text": "meta stuff goes to a meta root", "ruling": "abc1234", "about": "drive", "to": "root", "order": rewrite}
    dashboard = {"kind": "owner", "text": "the dashboard drops my cards", "about": "drive", "thread": THREAD, "order": "o2"}
    product = {"kind": "owner", "text": "and the logs page", "about": "his work", "thread": THREAD}
    beside = {"kind": "owner", "text": "also ship the alert fix", "about": "his work", "thread": tooling}
    tools.run("record", demo, {"records": [defect, ruling, dashboard, product, beside]})
    with pytest.raises(DriveError, match='refused, nothing posted: an owner record names what his words are about, about "drive" for drive tooling'):
        tools.run("record", demo, {"records": [{"kind": "owner", "text": "cards say why"}]})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "tooler")
    run("start", demo, "--role", "meta")
    with pytest.raises(DriveError, match="a defect it hits is its own: one job call adds its order and reader, by drive manual meta defect"):
        tools.run("record", demo, {"records": [defect]})
    tools.run("record", demo, {"records": [{key: value for key, value in ruling.items() if key != "order"} | {"text": "the rewrite is o3; your cards change"}]})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    tools.run("record", demo, {"records": [defect]})
    assert [(lane, [(record["kind"], record["to"]) for record in records]) for lane, records in posted] == [
        ("communicator", [("defect", ["meta@demo"]), ("owner", ["meta@demo"]), ("owner", ["meta@demo"]), ("owner", ["root@demo"]), ("owner", ["root@demo"])]),
        ("meta", [("owner", ["root@demo"])]),
        ("root", [("defect", ["meta@demo"])]),
    ]
    assert [record["attr"]["about"] for record in posted[0][1][1:]] == ["drive", "drive", "his work", "his work"]
    assert [{key: record["attr"].get(key) for key in ("order", "part")} for record in posted[0][1][1:]] == [
        {"order": "o3", "part": None},
        {"order": "o2", "part": "o2:the dashboard drops my cards"},
        {"order": "o1", "part": "o1:and the logs page"},
        {"order": None, "part": None},
    ]
    orders = state.load(demo)["orders"]
    assert (orders["o3"]["text"], orders["o3"]["owner"], list(orders["o2"]["parts"])) == (rewrite["words"], "meta", ["Drive tooling: cards say why", "the dashboard drops my cards"])
    assert list(orders["o1"]["parts"]) == ["ship the run page", "and the logs page"]


def test_his_ruling_typed_into_the_communicators_terminal_goes_out_at_terminal_and_words_he_never_typed_there_are_refused(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, recorded: Answers, tmp_path: Path
) -> None:
    session = "communicator-session"
    drafted = "answer each of these within a minute"
    rows = [
        {"type": "user", "origin": {"kind": "channel"}, "message": {"role": "user", "content": f'<channel source="cc-inbox">{drafted}</channel>'}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "record", "input": {"kind": "human", "text": drafted}}]}},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": TYPED, "commandMode": "prompt", "origin": {"kind": "human"}}},
        {"type": "user", "origin": {"kind": "human"}, "isMeta": True, "message": {"role": "user", "content": drafted}},
    ]
    writing = {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed", "message": {"role": "user", "content": [{"type": "text", "text": drafted}]}}
    transcript = tmp_path / "config" / "projects" / "-work-demo" / f"{session}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("".join(f"{json.dumps(row, separators=(',', ':'))}\n" for row in rows) + json.dumps(writing, separators=(",", ":")))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(settled, "unsaid", UNSAID)
    recorded.shown["07fe4ea"] = {"id": "07fe4ea", "title": "Owner: answers come too slowly", "body": f"{TYPED}\nNotes: cci owner 681263", "tags": []}
    recorded.shown["d7af7ed"] = {"id": "d7af7ed", "title": "Owner: answer within a minute", "body": drafted, "tags": []}
    posted: list[tuple[str, list[dict]]] = []

    def post(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        records = json.loads(kwargs["input"])
        posted.append((argv[argv.index("--lane") + 1], records))
        return subprocess.CompletedProcess(argv, 0, "".join(f"#{n}\n" for n, _ in enumerate(records)), "")

    monkeypatch.setattr(subprocess, "run", post)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    run("start", demo, "--role", "communicator")
    order = {"words": "Drive tooling: answer things like this almost at once", "title": "Answer at once"}
    ruling = {"kind": "owner", "text": TYPED, "url": "terminal", "ruling": "07fe4ea", "about": "drive", "order": order}
    for words, answer in ((drafted, "07fe4ea"), (TYPED, "d7af7ed")):
        with pytest.raises(DriveError, match=f'1: refused, nothing posted: no prompt he typed into a terminal of the drive\'s sessions says "{drafted}"'):
            tools.run("record", demo, {"records": [ruling | {"text": words, "ruling": answer}]})
    assert posted == []
    tools.run("record", demo, {"records": [ruling]})
    assert [(lane, [(record["to"], record["url"], record["attr"]["ruling"]) for record in records]) for lane, records in posted] == [
        ("communicator", [(["meta@demo"], "terminal", "07fe4ea")])
    ]


def test_a_card_his_terminal_words_close_links_the_card_itself_and_one_his_slack_words_close_links_them(monkeypatch: pytest.MonkeyPatch) -> None:
    answered: list[tuple] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: answered.append(argv) or {"answered": True})
    superseded.answer_card(CARD, TYPED, "terminal", settled=True)
    superseded.answer_card(CARD, TYPED, UNDER_CARD, settled=False)
    assert answered == [
        ("card-answer", "--card", CARD, "--label", TYPED, "--settled", "--other", "--via", CARD),
        ("card-answer", "--card", CARD, "--label", TYPED, "--withdrawn", "--via", UNDER_CARD),
    ]


def test_his_badge_ask_of_record_672014_reaches_the_meta_session_holding_the_drive_tooling_order_its_record_call_adds(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[dict] = []
    cci = {"up": False}

    def post(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] != ["cci", "post"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if not cci["up"]:
            return subprocess.CompletedProcess(argv, 1, "", "cci: daemon unreachable")
        posted.extend(json.loads(str(kwargs["input"])))
        return subprocess.CompletedProcess(argv, 0, f"{672014 + len(posted)}\n", "")

    monkeypatch.setattr(subprocess, "run", post)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    thread = "1794480049.653959"
    asked = {
        "kind": "owner",
        "text": (
            "there's still 4 open including one from yesterday is that correct? also we should have an equivalent bade gfor bug"
            " reports that tracks all teh way trhough to letting the user know its live and fixed"
        ),
        "url": f"https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794480122672259?thread_ts={thread}&cid=D0C4HQWPLCC",
        "thread": thread,
        "about": "drive",
    }
    with pytest.raises(DriveError, match="1: refused, his words about the drive reach the meta session holding an order, and no order holds this record or its thread"):
        tools.run("record", demo, {"records": [asked]})
    badge = {"words": "Drive tooling: bug reports get a badge tracking each one until its reporter hears the fix is live", "title": "Bug report badge"}
    with pytest.raises(DriveError, match="1: refused, his words about the drive go to an order the meta session holds, so leave order.owner out or name it meta"):
        tools.run("record", demo, {"records": [asked | {"order": badge | {"owner": "coordinator"}}]})
    dry = tools.run("record", demo, {"records": [asked | {"order": badge}], "dry_run": True}).splitlines()
    assert dry[0].startswith("1 drive order add 'Drive tooling: bug reports get a badge")
    assert (posted, state.load(demo)["orders"]) == ([], {})
    with pytest.raises(DriveError) as down:
        tools.run("record", demo, {"records": [asked | {"order": badge}]})
    assert str(down.value).splitlines()[0] == "1 order add: o1"
    cci["up"] = True
    assert tools.run("record", demo, {"records": [asked | {"order": badge}]}).splitlines() == [
        "1 order add: o1; o1 already holds these words, so no order was added",
        "1 owner 672015",
    ]
    order = state.load(demo)["orders"]["o1"]
    assert (order["text"], order["owner"], order["from"], order["thread"], order["project"], order["workstream"]) == (
        badge["words"], "meta", "slack:D0C4HQWPLCC/1794480122.672259", ["D0C4HQWPLCC", thread], "The drive", "Drive tooling"
    )
    more = asked | {"text": "and count the ones from yesterday too", "url": f"https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794480200000100?thread_ts={thread}"}
    assert tools.run("record", demo, {"records": [more]}).splitlines()[-1] == "1 owner 672016"
    assert tools.run("record", demo, {"records": [more]}).splitlines() == ["1 owner 672017"]
    assert (list(state.load(demo)["orders"]), list(state.load(demo)["orders"]["o1"]["parts"])) == (["o1"], [state.whole_order(badge["words"]), more["text"]])
    assert [(record["to"], record["attr"]["order"], record["attr"].get("part")) for record in posted] == [
        (["meta@demo"], "o1", None),
        (["meta@demo"], "o1", f"o1:{more['text']}"),
        (["meta@demo"], "o1", f"o1:{more['text']}"),
    ]
    with pytest.raises(DriveError, match="1: refused, nothing posted: order is the order the communicator's owner record about the drive goes to"):
        tools.run("record", demo, {"records": [{"kind": "owner", "text": "ship the run page", "about": "his work", "order": badge}]})


def test_his_hierarchical_coordinators_question_in_his_own_words_reaches_the_meta_session_holding_order_o792(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[dict] = []

    def post(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] != ["cci", "post"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        posted.extend(json.loads(str(kwargs["input"])))
        return subprocess.CompletedProcess(argv, 0, f"{677100 + len(posted)}\n", "")

    monkeypatch.setattr(subprocess, "run", post)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    thread = "1794513118.807399"
    words = "did we move to heirachical coordinators? feels like it should fix tehse delays?"
    url = f"https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794514438699909?thread_ts={thread}&cid=D0C4HQWPLCC"
    asked = {"kind": "owner", "about": "drive", "text": words, "url": url, "thread": thread}
    run("order", "add", "--owner", "coordinator", "--title", "Subagents persist", "--project", "Iris", "--workstream", "Agents", "--thread", thread, "why is his subagents not persisting across restart?")
    with pytest.raises(DriveError, match="1: refused, his words about the drive reach the meta session holding an order, and o1 is no open order the meta session holds"):
        tools.run("record", demo, {"records": [asked]})
    filed = ["--kind", "answer", "--project", "The drive", "--workstream", "Skill and process", "--title", "Drive tooling: hierarchical coordinators"]
    assert run("order", "add", "--from", url, "--thread", thread, "--owner", "meta", *filed, words) == (0, "o2")
    assert tools.run("record", demo, {"records": [asked | {"attrs": {"order": "o2"}}]}).splitlines() == ["1 owner 677101"]
    assert tools.run("record", demo, {"records": [asked]}).splitlines() == ["1 owner 677102"]
    later = {"kind": "owner", "about": "drive", "text": "or one coordinator per project?", "thread": "1794520000.000100"}
    question = {"words": later["text"], "title": "Coordinator per project", "kind": "answer", "project": "The drive", "workstream": "Skill and process"}
    assert tools.run("record", demo, {"records": [later | {"order": question}]}).splitlines() == ["1 order add: o3", "1 owner 677103"]
    assert tools.run("record", demo, {"records": [later | {"order": question}]}).splitlines() == ["1 order add: o3; o3 already holds these words, so no order was added", "1 owner 677104"]
    orders = state.load(demo)["orders"]
    assert [(order_id, orders[order_id]["owner"], orders[order_id]["kind"], list(orders[order_id]["parts"])) for order_id in ("o2", "o3")] == [
        ("o2", "meta", "answer", [state.whole_order(words)]),
        ("o3", "meta", "answer", [later["text"]]),
    ]
    assert [(record["to"], record["attr"]["order"], record["attr"].get("part")) for record in posted] == [
        (["meta@demo"], "o2", f"o2:{state.whole_order(words)}"),
        (["meta@demo"], "o2", f"o2:{state.whole_order(words)}"),
        (["meta@demo"], "o3", None),
        (["meta@demo"], "o3", None),
    ]


def test_his_words_carrying_on_an_order_of_his_work_are_its_parts_the_watch_names_until_a_job_carries_each(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = "1794521367.852649"
    run("order", "add", "--title", "Writer handover", "--project", "Iris", "--workstream", "Durable agents", "--thread", thread, "the SandSQL writer handover pauses writes", "--parts", state.IMMEDIATE_FIX)
    assert run("job", "add", "builder", "--order", "o1", "--part", state.IMMEDIATE_FIX, "--goal", "merge the handover stack", "--agent", "builder-1") == (0, "j1")
    posted: list[dict] = []

    def post(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["cci", "post"]:
            posted.extend(json.loads(str(kwargs["input"])))
        return subprocess.CompletedProcess(argv, 0, f"#{len(posted)}\n", "")

    monkeypatch.setattr(subprocess, "run", post)
    open_cards(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    dm = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC"
    iterate = {"kind": "owner", "text": "rase the 1s test for now then keep iterating", "url": f"{dm}/p1794526841094869?thread_ts={thread}", "thread": thread, "about": "his work"}
    options = {"kind": "owner", "text": "YOU SAID YOU WERE BRINGING ME OPTIONS FOR 0 STALL", "url": f"{dm}/p1794528706341939?thread_ts={thread}", "thread": thread, "about": "his work"}
    for words in (iterate, options, iterate):
        tools.run("record", demo, {"records": [words]})
    assert list(state.load(demo)["orders"]["o1"]["parts"]) == [state.IMMEDIATE_FIX, iterate["text"], options["text"]]
    assert [(record["to"], record["attr"]["order"], record["attr"]["part"]) for record in posted] == [
        (["root@demo"], "o1", f"o1:{words['text']}") for words in (iterate, options, iterate)
    ]
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=state.now())
    later = state.now() + timedelta(minutes=6)
    assert watch.orphan_lines(watching, later, set()) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    unstaffed = [f'ORPHAN o1 "{words["text"]}": no job' for words in (iterate, options)]
    assert watch.orphan_lines(watching, later, set()) == unstaffed
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    carried = ["--part", iterate["text"], "--part", options["text"]]
    assert run("job", "add", "reader", "--order", "o1", *carried, "--goal", "options for a zero-stall writer handover", "--agent", "reader-1") == (0, "j2")
    assert watch.orphan_lines(watching, later + timedelta(minutes=10), set()) == []
    assert [part["orphan_said"] for part in state.load(demo)["orders"]["o1"]["parts"].values()] == [None, None, None]


def test_the_communicators_owner_or_answer_record_goes_to_the_session_hearing_its_jobs_or_threads_order_whatever_lane_it_names(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Alerts", "fix the alert")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    other = "1794200000.000200"
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"], data["orders"]["o2"]["thread"] = ["D123", THREAD], ["D123", other]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    records = [
        {"kind": "owner", "text": "also the logs page", "thread": THREAD, "to": "root", "about": "his work"},
        {"kind": "owner", "text": "and the alert", "thread": other, "to": "iris", "about": "his work"},
        {"kind": "answer", "text": "Ring", "re": "7", "attrs": {"job": "j1"}, "thread": other},
        {"kind": "defect", "text": "cc-slack say fails", "thread": THREAD},
    ]
    open_cards(monkeypatch, "demo:j1-7")
    said = tools.run("record", demo, {"records": records, "dry_run": True}).splitlines()
    assert said[:2] == ["1 drive order set o1 --parts 'ship the run page' 'also the logs page'", "2 drive order set o2 --parts 'fix the alert' 'and the alert'"]
    assert [(json.loads(line.split(": ", 1)[1])["kind"], json.loads(line.split(": ", 1)[1])["to"]) for line in said[2:6]] == [
        ("owner", ["iris@demo"]),
        ("owner", ["root@demo"]),
        ("answer", ["iris@demo"]),
        ("defect", ["meta@demo"]),
    ]
    assert said[6:] == ['3 would end j1\'s question "Which ring?" answered, closing its card']


def test_a_handlers_handoff_goes_to_the_session_holding_the_order_its_order_attr_names(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Alerts", "fix the alert")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: cards say why")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    with state.edit(demo) as data:
        data["handlers"]["h1"] = {"session": "handler-1", "transcript": None, "started": state.stamp(), "holds": [], "handed": [], "posted": []}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "handler-1")
    records = [{"kind": "handoff", "text": "redraw the run page card with both PRs", "attrs": {"order": order}} for order in ("o1", "o2", "o3")]
    said = tools.run("record", demo, {"records": records, "dry_run": True}).splitlines()
    assert [(line.split(": ", 1)[0], json.loads(line.split(": ", 1)[1])["to"]) for line in said] == [
        ("1 communicator", ["iris@demo"]),
        ("2 communicator", ["root@demo"]),
        ("3 communicator", ["meta@demo"]),
    ]


def open_cards(monkeypatch: pytest.MonkeyPatch, *waits: str, prs: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """His open question card for each waits id, the first at CARD, its question kept on the job the waits id names as drawn there, and a PR card at PR_CARD carrying the PRs named, stored as the drive stores the cards cc-slack lists; each card-answer call cc-slack gets, answered as a card that was waiting."""
    urls = [CARD if index == 0 else f"{CARD[:-2]}{index:02d}" for index in range(len(waits))]
    if waits:
        with state.edit("demo") as data:
            for wait, url in zip(waits, urls, strict=True):
                job_id = wait.partition(":")[2].partition("-")[0]
                data["jobs"][job_id]["questions"].append(state.new_question(None, "Which ring?", state.stamp(), card=url) | {"waits": wait})
    found = {
        "team_url": "",
        "questions": [
            {"card": {"waits": wait, "title": "Which ring?", "body": "Ring or knock?", "options": ["Ring", "Knock"], "recommended": "Ring"}, "url": url, "question": "Which ring?", "options": ["Ring", "Knock"]}
            for wait, url in zip(waits, urls, strict=True)
        ],
        "closed": [], "pr_cards": [{"url": PR_CARD, "prs": list(prs), "card": {"options": ["Approve", "Hold"]}}] if prs else [],
    }
    monkeypatch.setattr(view, "cards", lambda name: found)
    view.refresh_cards("demo")
    answered: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: answered.append(argv) or {})
    return answered


def test_his_answer_waits_on_each_job_it_answers_until_that_job_resumes(demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess([], 0, "#61\n#62\n", ""))
    open_cards(monkeypatch, "demo:j1-7")
    answers = [{"kind": "answer", "text": "the lazy copy, no sweeper", "url": UNDER_CARD, "waits": "demo:j1-7"}, {"kind": "answer", "text": "Ring", "waits": "demo:j1-8", "undone": True}]
    tools.run("record", demo, {"records": answers})
    [kept] = state.load(demo)["jobs"]["j1"]["undelivered"]
    assert (kept["seq"], kept["words"], kept["said"]) == ("#61", "the lazy copy, no sweeper", None)


def test_his_words_on_an_open_card_end_its_question_and_close_it_in_the_same_call_that_sends_them(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the page")
    posted: list[list[dict]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append(json.loads(kwargs["input"])) or subprocess.CompletedProcess([], 0, "#51\n", ""))
    answered = open_cards(monkeypatch, "demo:j1-7", "demo:j2-8")
    answer = {"kind": "answer", "text": "the lazy copy, no sweeper", "url": UNDER_CARD, "waits": "demo:j1-7"}
    assert tools.run("record", demo, {"records": [answer], "dry_run": True}).splitlines()[1:] == ['1 would end j1\'s question "Which ring?" answered, closing its card']
    assert answered == [] and posted == []
    assert tools.run("record", demo, {"records": [answer]}).splitlines() == [
        "1 answer #51",
        '1 j1\'s question "Which ring?" ended answered by #51',
        f"demo:j1-7: his card {CARD} now shows his words with no buttons, its question answered",
    ]
    assert answered == [("card-answer", "--card", CARD, "--label", "the lazy copy, no sweeper", "--settled", "--other", "--via", UNDER_CARD)]
    rework = {"kind": "owner", "text": "So then why didn't you withdraw this card?", "url": f"{CARD[:-2]}01?thread_ts=1794200000.000701", "thread_card": "demo:j2-8", "about": "his work"}
    tools.run("record", demo, {"records": [rework]})
    assert answered[-1] == ("card-answer", "--card", f"{CARD[:-2]}01", "--label", rework["text"], "--withdrawn", "--via", rework["url"])
    assert posted[-1][0]["attr"] == {"thread_card": "demo:j2-8", "about": "his work"}
    assert [question["closed"]["how"] for job_id in ("j1", "j2") for question in state.load(demo)["jobs"][job_id]["questions"]] == ["answered", "withdrawn"]
    with pytest.raises(DriveError, match="1: refused, nothing posted: the question this answer names ended already"):
        tools.run("record", demo, {"records": [answer]})
    with pytest.raises(DriveError, match="1: refused, nothing posted: no job of this drive keeps the question this answer names"):
        tools.run("record", demo, {"records": [{"kind": "answer", "text": "Ring", "waits": "demo:j3-9"}]})
    assert len(posted) == 2


def test_his_owner_words_under_a_question_card_end_its_question_when_about_its_work_and_his_drive_feedback_leaves_a_product_card_asking(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: cards say why")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    with monkeypatch.context() as tooler:
        tooler.setenv("CLAUDE_CODE_SESSION_ID", "tooler")
        run("start", demo, "--role", "meta")
        run("job", "add", "builder", "--order", "o2", "--goal", "build the cards")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess([], 0, "#61\n", ""))
    answered = open_cards(monkeypatch, "demo:j1-7", "demo:j2-8")
    feedback = {"kind": "owner", "text": "wat the fuck thats not acceptbale", "url": UNDER_CARD, "about": "drive"}
    assert tools.run("record", demo, {"records": [feedback]}) == "1 owner #61"
    assert (answered, view.open_questions(state.load(demo)["jobs"]["j1"]) != []) == ([], True)
    rework = {"kind": "owner", "text": "no, keep the runs flat", "url": UNDER_CARD, "about": "his work"}
    assert tools.run("record", demo, {"records": [rework]}).splitlines() == [
        "1 owner #61",
        '1 j1\'s question "Which ring?" ended withdrawn by #61',
        f"demo:j1-7: his card {CARD} now shows his words with no buttons, its question withdrawn",
    ]
    tooling = {"kind": "owner", "text": "this card should say why", "url": f"{CARD[:-2]}01?thread_ts=1794200000.000701", "about": "drive"}
    tools.run("record", demo, {"records": [tooling]})
    assert answered == [
        ("card-answer", "--card", CARD, "--label", rework["text"], "--withdrawn", "--via", UNDER_CARD),
        ("card-answer", "--card", f"{CARD[:-2]}01", "--label", tooling["text"], "--withdrawn", "--via", tooling["url"]),
    ]


def test_an_answer_naming_his_open_card_by_its_waits_attr_settles_that_card_in_the_same_call(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the page")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess([], 0, "#52\n", ""))
    answered = open_cards(monkeypatch, "demo:j1-7", "demo:j2-8")
    card = f"{CARD[:-2]}01"
    said = tools.run("record", demo, {"records": [{"kind": "answer", "text": "Ring", "attrs": {"waits": "demo:j2-8"}}]})
    assert said.splitlines() == ["1 answer #52", '1 j2\'s question "Which ring?" ended answered by #52', f"demo:j2-8: his card {card} now shows his words with no buttons, its question answered"]
    assert answered == [("card-answer", "--card", card, "--label", "Ring", "--settled", "--other", "--via", card)]


def test_an_answer_closing_an_ask_his_recorded_rules_settled_before_any_card_drew_settles_its_kept_question(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, heard: Heard
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Durable agents", "one card per subject")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    asked = str(state.drive_dir(demo) / "asks" / "j1-5c1d.md")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(asked, "Which card keeps the subject?", state.stamp())]
    shown = {"ask": asked}
    posted: list[list[dict]] = []

    def cci(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["cci", "show"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"kind": "ask", "fields": {"attrs": shown | {"job": "j1"}}}), "")
        posted.append(json.loads(str(kwargs["input"])))
        return subprocess.CompletedProcess(argv, 0, "672342\n", "")

    monkeypatch.setattr(subprocess, "run", cci)
    open_cards(monkeypatch)
    answer = {"kind": "answer", "text": "one subject has one card", "waits": "demo:j1-672331", "ruling": "320b7a7"}
    shown["ask"] = str(state.drive_dir(demo) / "asks" / "j1-other.md")
    with pytest.raises(DriveError, match="1: refused, nothing posted: no job of this drive keeps the question this answer names"):
        tools.run("record", demo, {"records": [answer]})
    shown["ask"] = asked
    heard.refusal = "no permalink names where he said it"
    with pytest.raises(DriveError, match="1: refused, nothing posted: no permalink names where he said it"):
        tools.run("record", demo, {"records": [answer]})
    assert heard.checked == [("one subject has one card", None)]
    heard.refusal = None
    assert tools.run("record", demo, {"records": [answer], "dry_run": True}).splitlines()[1:] == ['1 would end j1\'s question "Which card keeps the subject?" answered, closing ask 672331']
    assert posted == []
    assert tools.run("record", demo, {"records": [answer]}).splitlines()[0] == "1 answer 672342"
    assert posted[0][0]["resolves"] == ["672331"]
    job = state.load(demo)["jobs"]["j1"]
    closed = job["questions"][0]["closed"]
    assert ((closed["how"], closed["words"], closed["seq"], closed["ruling"]), view.open_questions(job)) == (("answered", "one subject has one card", "672342", "320b7a7"), [])


def test_his_answer_settling_a_finished_readers_question_reaches_the_builder_at_work_on_its_research_and_never_the_reader(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, recorded: Answers
) -> None:
    recorded.shown["73060f0"] = ANSWER
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Durable agents", "one card per subject", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the card")
    state.keep_report(demo, "j1", "DONE researched the store copy", RESEARCH)
    first, second = (str(state.drive_dir(demo) / "asks" / f"j1-{key}.md") for key in ("5c1d", "9e2f"))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", questions=[state.new_question(first, "Which card keeps the subject?", state.stamp())])
        data["jobs"]["j1"]["undelivered"] = [{"seq": "673013", "words": "one card", "at": state.stamp(), "said": None}]
    assert state.load(demo)["jobs"]["j1"]["undelivered"] == []
    shown = {"ask": first}

    def cci(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["cci", "show"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"kind": "ask", "fields": {"attrs": shown | {"job": "j1"}}}), "")
        return subprocess.CompletedProcess(argv, 0, f"{shown['seq']}\n", "")

    monkeypatch.setattr(subprocess, "run", cci)
    open_cards(monkeypatch)
    shown["seq"] = "673125"
    tools.run("record", demo, {"records": [{"kind": "answer", "text": "one subject has one card", "waits": "demo:j1-673100", "ruling": "73060f0"}]})
    assert state.load(demo)["jobs"]["j1"]["undelivered"] == []
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    tools.run("job", demo, {"ops": [{"brief": {"id": "j2", "serves": "his order", "done_when": "installed", "where": "yasyf/cc-skills main"}}]})
    written = (state.drive_dir(demo) / "jobs" / "j2.brief.md").read_text()
    assert f"Decisions that bind:\n- {ANSWER['title']} (73060f0)\n" in written and "\nResearched: j1\n" in written
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=state.now())
    assert watch.undelivered_lines(watching, state.now() + timedelta(minutes=11)) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(second, "Which card keeps the thread?", state.stamp()))
    shown.update(ask=second, seq="673200")
    tools.run("record", demo, {"records": [{"kind": "answer", "text": "the thread's own card", "waits": "demo:j1-673190"}]})
    drive = state.load(demo)
    assert ([kept["seq"] for kept in drive["jobs"]["j2"]["undelivered"]], drive["jobs"]["j1"]["undelivered"]) == (["673200"], [])
    assert [line.split(":")[0] for line in watch.undelivered_lines(watching, state.now() + timedelta(minutes=11))] == ["ANSWER undelivered j2"]


def test_his_owner_words_under_a_pr_card_leave_it_up_since_only_his_order_on_its_thread_withdraws_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    pr = "https://github.com/Forge-AI/monorepo/pull/54084"
    run("pr", "add", pr, "--job", "j1")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess([], 0, "#61\n", ""))
    answered = open_cards(monkeypatch, prs=(pr,))
    asked = {"kind": "owner", "text": "Why is this card gone when the PR is approved?", "url": f"{PR_CARD}?thread_ts=1794200000.000900", "about": "his work"}
    assert tools.run("record", demo, {"records": [asked]}) == "1 owner #61"
    assert (withdrawals.asked, answered) == ([], [])
    pr_entry = state.load(demo)["prs"]["Forge-AI/monorepo#54084"]
    assert (pr_entry["wants_tap"], pr_entry["rework"]) == (True, None)


def test_a_brief_carries_his_order_and_each_rulings_words_without_the_drives_notes(demo: str, run: Callable[..., tuple[int, str]], recorded: Answers) -> None:
    recorded.shown["73060f0"] = ANSWER
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "do a pass on the tools", "--from", f"slack:D123/{THREAD}")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    run("job", "add", "builder", "--order", "o1", "--goal", "make the verbs MCP tools")
    brief = {"id": "j2", "serves": "drive tooling", "done_when": "installed", "where": "yasyf/cc-skills main", "ruling": ["73060f0"]}
    said = tools.run("job", demo, {"ops": [{"brief": brief}]})
    path = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    assert said == (
        f"1 job brief: Read your brief first: {path}; his words from o1, rulings: 1, earlier jobs: 0; o1 is the meta session's order now,"
        " since a builder serving drive tooling is its alone: only the meta session starts j2 and its agents and posts its reports;"
        " from the coordinator, start no agent on it and send meta@demo a defect record naming j2 and its `Read your brief first:` line"
    )
    assert state.load(demo)["orders"]["o1"]["owner"] == "meta"
    written = path.read_text()
    assert written.startswith("Order (his words): do a pass on the tools\nServes: drive tooling\nGoal: make the verbs MCP tools\n")
    assert f"Decisions that bind:\n- {ANSWER['title']} (73060f0)\n  {ANSWER['body'].splitlines()[0]}\n" in written
    assert "cci owner 660781" not in written
    assert written.endswith("Drive: demo, order o1, job j2\n")
    assert state.load(demo)["jobs"]["j2"]["brief"] == str(path)


def test_ops_or_their_items_sent_as_json_text_reach_the_tool_as_the_lists_they_spell() -> None:
    shapes = {tool["name"]: tool["inputSchema"]["properties"] for tool in mcp.tool_list()}
    ops = [{"set": {"url": "https://github.com/Forge-AI/monorepo/pull/42", "tap": True}}]
    assert tools.parsed_arguments({"ops": json.dumps(ops)}, shapes["pr"]) == {"ops": ops}
    assert tools.parsed_arguments({"ops": [json.dumps(ops[0])], "dry_run": True}, shapes["pr"]) == {"ops": ops, "dry_run": True}
    assert tools.parsed_arguments({"text": "[a line, not a list]"}, shapes["say"]) == {"text": "[a line, not a list]"}
    with pytest.raises(DriveError, match="ops is a list or object"):
        tools.parsed_arguments({"ops": "[{"}, shapes["pr"])
