import io
import json
from collections.abc import Callable
from pathlib import Path

import landing_guard
import pytest
from conftest import SESSION, SHORT_ASK
from drive import agents, limits, shell, state, view
from drive.shell import Stdin

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
SIBLINGS = ("https://github.com/Forge-AI/monorepo/pull/41", "https://github.com/Forge-AI/monorepo/pull/43")
CARD = "demo:j1-661229"
LIVE = "https://github.com/yasyf/cc-skills/blob/3d8111f/pr-shots/live-hover.png?raw=true"
DEMO = "https://github.com/yasyf/cc-skills/blob/3d8111f/pr-shots/demo-hover/x.png?raw=true"


@pytest.fixture
def drive_pr(run: Run, demo: str) -> str:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("pr", "add", PR, "--job", "j1")
    return state.pr_key(PR)


@pytest.fixture
def no_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def record(args: list[str], cwd: str) -> str:
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(landing_guard, "run", record)


def bash(command: str, cwd: str = "/work", **fields: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "session_id": SESSION, "transcript_path": "/work/session.jsonl", **fields}


def approve(key: str) -> None:
    with state.edit("demo") as data:
        data["prs"][key]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}


@pytest.mark.parametrize(
    ("command", "step"),
    [
        ('ccx vcs ship -m "Fix: x"', "ccx vcs ship"),
        ("git commit -am x", "git commit -am"),
        ("for lane in a b; do git push origin $lane; done", "git push origin"),
        ("bash -c 'go build ./...'", "go build ./..."),
    ],
)
def test_the_coordinators_own_ship_commit_or_build_call_is_refused_naming_a_job(demo: str, no_calls: None, command: str, step: str) -> None:
    reason = landing_guard.deny_reason(bash(command, session_id=SESSION))
    assert reason == landing_guard.COORDINATOR_STEP.format(who="coordinator", step=step)
    assert "Start a job with this step" in reason
    assert "drive job add" in reason
    assert (landing_guard.judged_reason(bash(command, session_id=SESSION)), landing_guard.guarded_reason(bash(command, session_id=SESSION))) == (reason, None)


def test_the_meta_sessions_own_ship_call_is_refused_naming_a_job(demo: str, no_calls: None) -> None:
    with state.edit("demo") as data:
        data["roles"]["meta"] = data["roles"]["coordinator"] | {"session": "tooler"}
    assert landing_guard.deny_reason(bash('ccx vcs ship -m "Fix: x"', session_id="tooler")) == landing_guard.COORDINATOR_STEP.format(who="meta session", step="ccx vcs ship")


@pytest.mark.parametrize(
    "command",
    [
        "drive status o288",
        "orca orchestration worker-read --dispatch d1 --limit 50 --full --json",
        "cci show 661229 --json",
        "for j in j855 j966 j641; do drive job replies $j --drive demo | tail -5; done",
        "orca orchestration check --help",
        'orca orchestration send --to dispatch:ctx_7ac497ba6e6c --type status --subject "His answer" --body "Land it"',
    ],
)
def test_the_coordinators_reads_loops_and_orca_calls_pass(demo: str, no_calls: None, command: str) -> None:
    assert landing_guard.deny_reason(bash(command, session_id=SESSION)) is None


def test_a_coordinators_agent_and_another_session_may_ship(demo: str, no_calls: None) -> None:
    assert landing_guard.deny_reason(bash('ccx vcs ship -m "Fix: x"', session_id=SESSION, agent_id="a1")) is None
    assert landing_guard.deny_reason(bash('ccx vcs ship -m "Fix: x"', session_id="elsewhere")) is None


def test_landing_while_his_card_on_it_is_open_is_refused_naming_the_card_to_wait_on(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    with state.edit("demo") as data:
        data["prs"][drive_pr]["waits"] = [CARD]
    reason = landing_guard.deny_reason(bash("stack-enqueue 42"))
    assert reason == landing_guard.CARD_REASON.format(key=drive_pr, cards=CARD)
    assert f"return `WAITING {CARD}`" in reason
    with state.edit("demo") as data:
        data["prs"][drive_pr]["waits"] = []
        data["jobs"]["j1"]["waits"] = [CARD]
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) == reason


def test_landing_a_pr_whose_job_waits_on_its_own_and_a_sibling_drive_pr_passes_since_no_pr_is_his_card(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    sibling = state.pr_key(SIBLINGS[1])
    with state.edit("demo") as data:
        data["prs"][sibling] = state.new_pr(SIBLINGS[1], "j1", "o1")
        data["jobs"]["j1"]["waits"] = [drive_pr, sibling]
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None
    with state.edit("demo") as data:
        data["jobs"]["j1"]["waits"].append(CARD)
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) == landing_guard.CARD_REASON.format(key=drive_pr, cards=CARD)


def test_the_immediate_fix_lands_while_his_card_on_the_proper_fix_is_open(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    with state.edit("demo") as data:
        data["jobs"]["j1"] |= {"waits": [CARD], "parts": [state.IMMEDIATE_FIX]}
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


def test_landing_once_he_answered_the_card_passes(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    with state.edit("demo") as data:
        data["prs"][drive_pr]["waits"] = [CARD]
    state.cards_path("demo").write_text(json.dumps({"source": "live", **view.NO_CARDS}))
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 42 -R Forge-AI/monorepo --squash",
        "gh pr edit 42 -R Forge-AI/monorepo --add-label merge-fast",
        "gh pr edit 42 -R Forge-AI/monorepo --add-label merge",
        "gh api -X PUT repos/Forge-AI/monorepo/pulls/42/merge -f merge_method=squash",
    ],
)
def test_a_tapped_monorepo_pr_landed_around_the_queue_is_refused_naming_stack_enqueue(drive_pr: str, no_calls: None, command: str) -> None:
    approve(drive_pr)
    reason = landing_guard.deny_reason(bash(command))
    assert reason == landing_guard.QUEUE_REASON.format(key=drive_pr)
    assert "stack-enqueue" in reason
    assert landing_guard.deny_reason(bash(command, session_id="ordinary")) is None


def test_a_tapped_monorepo_pr_enqueued_or_an_incident_fix_merged_passes(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(".agents/skills/submit-pr/scripts/stack-enqueue 42")) is None
    with state.edit("demo") as data:
        data["prs"][drive_pr]["exempt"] = "incident fix"
    assert landing_guard.deny_reason(bash("gh pr merge 42 -R Forge-AI/monorepo --squash")) is None


def test_a_merge_in_a_for_loop_is_checked_in_the_folder_each_value_of_its_cd_target_names(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    origins = {"/work/cc-slack/pin": "https://github.com/yasyf/cc-slack.git", "/work/monorepo/pin": "git@github.com:Forge-AI/monorepo.git"}
    read_in: list[str] = []

    def origin(args: list[str], cwd: str) -> str:
        assert args == ["git", "remote", "get-url", "origin"]
        read_in.append(cwd)
        return origins[cwd]

    monkeypatch.setattr(landing_guard, "run", origin)
    loop = "for r in cc-slack monorepo; do cd /work/$r/pin && gh pr merge 42 --squash 2>&1 | tail -2; done"
    assert landing_guard.deny_reason(bash(loop, session_id="ordinary")).startswith(f"{drive_pr}: no tap from him yet")
    assert read_in == ["/work/cc-slack/pin", "/work/monorepo/pin"]
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(loop, session_id="ordinary")) is None


def test_a_ship_in_a_for_loop_checks_the_body_file_each_value_names(
    demo: str, monkeypatch: pytest.MonkeyPatch, visibilities: dict, tmp_path: Path
) -> None:
    monkeypatch.setattr(landing_guard, "run", lambda args, cwd: "https://github.com/yasyf/cc-skills.git")
    visibilities["yasyf/cc-skills"] = "public"
    for name, picture in (("cc-inbox", DEMO), ("cc-notes", LIVE)):
        (tmp_path / name).mkdir()
        (tmp_path / f"{name}-body.md").write_text(f"## Show me\n![Hover]({picture})\n")
    loop = f"for r in cc-inbox cc-notes; do cd {tmp_path}/$r && ccx vcs ship --no-watch --pr-body-file {tmp_path}/$r-body.md; done"
    assert landing_guard.deny_reason(bash(loop, agent_id="a1")).startswith(
        f"Refused on purpose, before it goes up: yasyf/cc-skills is public, and its body shows pictures outside a demo or public path ({LIVE})"
    )
    (tmp_path / "cc-notes-body.md").write_text(f"## Show me\n![Hover]({DEMO})\n")
    assert landing_guard.deny_reason(bash(loop, agent_id="a1")) is None


@pytest.mark.parametrize(
    "command",
    [
        "for owner in Forge-AI; do\n  for n in 7 42; do gh pr merge $n -R $owner/monorepo; done\ndone",
        'pr=42 && gh pr merge "$pr" -R Forge-AI/monorepo',
        "export REPO=Forge-AI/monorepo; gh pr merge 42 -R ${REPO}",
        "gh pr merge 42 -R $LANDING_REPO",
    ],
)
def test_a_merge_naming_a_parameter_the_script_or_session_sets_is_checked_with_its_value(
    drive_pr: str, no_calls: None, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setenv("LANDING_REPO", "Forge-AI/monorepo")
    assert landing_guard.deny_reason(bash(command, session_id="ordinary")).startswith(f"{drive_pr}: no tap from him yet")


@pytest.mark.parametrize(
    ("script", "commands"),
    [
        ("""cd /w/$r/x && echo "$r ok" '$r' \\$r ${r}y""", [["cd", "/w/a b/x"], ["echo", "a b ok", "$r", "$r", "a by"]]),
        ("gh pr create --title=\"New: $r\" -b '' -F \"\\$r.md\"", [["gh", "pr", "create", "--title=New: a b", "-b", "", "-F", "$r.md"]]),
        ("echo $unset ${r:-x} $(date)", [["echo", "$unset", "${r:-x}", "$"], ["date"]]),
    ],
)
def test_a_word_expands_its_parameters_where_zsh_does_and_keeps_a_quoted_or_unknown_one_as_written(script: str, commands: list[list[str]]) -> None:
    assert [[shell.expanded(word, {"r": "a b"}) for word in group] for group in shell.templates(script)] == commands


@pytest.fixture
def stored_stack(drive_pr: str, run: Run) -> list[str]:
    for url in SIBLINGS:
        run("pr", "add", url, "--job", "j1")
    keys = [state.pr_key(SIBLINGS[0]), drive_pr, state.pr_key(SIBLINGS[1])]
    for key in keys:
        approve(key)
    with state.edit("demo") as data:
        for key in keys:
            data["prs"][key] |= {"stack": keys, "native": keys}
    return keys


def test_enqueueing_the_bottom_of_a_stored_stack_alone_is_refused_naming_every_pr(stored_stack: list[str], no_calls: None) -> None:
    reason = landing_guard.deny_reason(bash("stack-enqueue 41"))
    assert reason == landing_guard.STACK_REASON.format(named=stored_stack[0], left=", ".join(stored_stack[1:]), every="41 42 43")
    assert reason.endswith("`stack-enqueue 41 42 43`")
    assert landing_guard.deny_reason(bash("stack-enqueue 41 2>&1 | tail -20")) == reason


def test_enqueueing_every_open_pr_of_a_stored_stack_passes(stored_stack: list[str], no_calls: None) -> None:
    assert landing_guard.deny_reason(bash("stack-enqueue 41 42 43")) is None
    assert landing_guard.deny_reason(bash("stack-enqueue 41 42 43 2>&1 | tail -20")) is None
    with state.edit("demo") as data:
        data["prs"][stored_stack[0]]["state"] = "merged"
    assert landing_guard.deny_reason(bash("stack-enqueue 42 43")) is None


def test_enqueueing_one_sibling_pr_of_his_order_card_alone_passes_since_stack_enqueue_lands_each_on_its_own(stored_stack: list[str], no_calls: None) -> None:
    with state.edit("demo") as data:
        for key in stored_stack:
            data["prs"][key]["native"] = []
    assert landing_guard.deny_reason(bash("stack-enqueue 41")) is None


def test_his_typed_approval_recorded_with_its_source_lets_the_pr_land(drive_pr: str, run: Run, no_calls: None) -> None:
    assert landing_guard.deny_reason(bash("stack-enqueue 42")).startswith(f"{drive_pr}: no tap from him yet")
    run("pr", "set", PR, "--tapped", "Approve", "--tap-source", "slack:https://acme.slack.com/archives/D123/p1794200000000100")
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


def body_post(tmp_path: Path, command: str, picture: str, **fields: object) -> dict:
    (tmp_path / "b.md").write_text(f"## Show me\n![Hover]({picture})\n")
    return bash(command, cwd=str(tmp_path), session_id=SESSION, agent_id="a1") | fields


@pytest.mark.parametrize(
    "command",
    ["gh pr create -R yasyf/cc-skills --title 'New: hover' --body-file b.md", "gh pr edit 7 --repo yasyf/cc-skills -F b.md"],
)
def test_a_drive_agents_public_pr_body_with_a_live_picture_is_refused_before_it_goes_up(
    demo: str, no_calls: None, visibilities: dict, tmp_path: Path, command: str
) -> None:
    visibilities["yasyf/cc-skills"] = "public"
    reason = landing_guard.deny_reason(body_post(tmp_path, command, LIVE))
    assert reason.startswith(
        f"Refused on purpose, before it goes up: yasyf/cc-skills is public, and its body shows pictures outside a demo or public path ({LIVE})"
    )
    assert reason.endswith("Fix the body, then run this again")
    assert landing_guard.deny_reason(body_post(tmp_path, command, DEMO)) is None
    assert landing_guard.deny_reason(body_post(tmp_path, command, LIVE, session_id="elsewhere")) is None


def test_a_drive_agents_public_pr_body_naming_a_teammate_is_refused_before_it_goes_up(
    demo: str, no_calls: None, visibilities: dict, tmp_path: Path
) -> None:
    visibilities["yasyf/cc-skills"] = "public"
    (tmp_path / "b.md").write_text("## Refresher\nRowan's report started this.\n")
    create = bash("gh pr create -R yasyf/cc-skills --title X --body-file b.md", cwd=str(tmp_path), session_id=SESSION, agent_id="a1")
    assert landing_guard.deny_reason(create).startswith(
        "Refused on purpose, before it goes up: yasyf/cc-skills is public, and its body names Rowan (Rowan Vale). "
    )
    (tmp_path / "b.md").write_text("## Refresher\nThe reporter's report started this.\n")
    assert landing_guard.deny_reason(create) is None


def test_a_pr_body_is_judged_by_the_repository_of_the_pr_it_names(demo: str, monkeypatch: pytest.MonkeyPatch, visibilities: dict, tmp_path: Path) -> None:
    monkeypatch.setattr(landing_guard, "run", lambda args, cwd: "https://github.com/yasyf/cc-skills.git")
    visibilities["yasyf/cc-skills"] = "public"
    assert landing_guard.deny_reason(body_post(tmp_path, f"gh pr edit {PR} -F b.md", LIVE)) is None
    public = "gh pr edit https://github.com/yasyf/cc-skills/pull/7 -F b.md"
    assert "yasyf/cc-skills is public" in landing_guard.deny_reason(body_post(tmp_path, public, LIVE))


def test_a_body_file_written_in_the_same_call_is_refused_until_it_exists(demo: str, no_calls: None, visibilities: dict, tmp_path: Path) -> None:
    visibilities["yasyf/cc-skills"] = "public"
    create = f"gh pr create -R yasyf/cc-skills --title X --body-file {tmp_path}/new.md"
    written = f"cat > {tmp_path}/new.md <<'EOF'\n## What you'll hate most\n![Live]({LIVE})\nEOF\n{create}"
    reason = landing_guard.deny_reason(bash(written, agent_id="a1"))
    assert reason.startswith(f"Refused on purpose, before it goes up: the body file {tmp_path}/new.md does not exist yet")
    assert reason.endswith("Write the file in a call of its own, then run this one")
    (tmp_path / "new.md").write_text(f"## Show me\n![Hover]({DEMO})\n")
    assert landing_guard.deny_reason(bash(create, agent_id="a1")) is None


def test_a_ship_with_a_public_pr_body_reads_its_repo_from_the_checkout(
    demo: str, monkeypatch: pytest.MonkeyPatch, visibilities: dict, tmp_path: Path
) -> None:
    monkeypatch.setattr(landing_guard, "run", lambda args, cwd: "https://github.com/yasyf/cc-skills.git")
    visibilities["yasyf/cc-skills"] = "public"
    command = "ccx vcs ship -m 'New: hover' --pr-body-file hover=b.md"
    assert "yasyf/cc-skills is public" in landing_guard.deny_reason(body_post(tmp_path, command, LIVE))
    assert landing_guard.deny_reason(body_post(tmp_path, command, DEMO)) is None
    visibilities["yasyf/cc-skills"] = "private"
    assert landing_guard.deny_reason(body_post(tmp_path, command, LIVE)) is None


def test_his_words_on_his_open_card_posted_with_cci_post_are_refused_for_the_record_tool(demo: str, no_calls: None) -> None:
    card = "https://forge.slack.com/archives/D123/p1794379200123456"
    shown = {"url": card, "question": "Which ring?", "asked": state.stamp(), "card": {"title": "Which ring?", "waits": "demo:j1-7"}}
    state.cards_path("demo").write_text(json.dumps({"source": "live", "team_url": "", "questions": [shown], "closed": [], "pr_cards": []}))
    answer = "cci post --kind answer --lane root --to communicator@demo --re 7 --attr job=j1 'the lazy copy'"
    assert f"his open card {card}" in landing_guard.deny_reason(bash(answer, agent_id="a1"))
    owner = f"cci post --kind owner --lane root --to communicator@demo --attr 'about=his work' --url '{card}?thread_ts=1794379200.123456' 'why is this card still up'"
    assert "the drive's `record` tool" in landing_guard.deny_reason(bash(owner, agent_id="a1"))
    assert landing_guard.deny_reason(bash("cci post --kind owner --lane root --to communicator@demo --attr 'about=his work' 'a new order'", agent_id="a1")) is None


def cci_delta(path: Path, field: str, names: list[str]) -> None:
    with path.open("a") as rows:
        rows.write(json.dumps({"type": "attachment", "attachment": {"type": "deferred_tools_delta", field: names}}) + "\n")


def test_a_drive_sessions_cci_post_in_bash_is_refused_for_the_cci_tools_while_its_cci_server_is_connected(demo: str, no_calls: None, tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    report = "cci post --kind report 'merged'"
    cci_delta(session, "addedNames", ["mcp__plugin_cc-inbox_cci__post", "mcp__plugin_cc-inbox_cci__close"])
    assert landing_guard.deny_reason(bash(report, transcript_path=str(session))) == landing_guard.CLI_POST
    for help_asked in ("cci post --help", "cci post -h", "cci post --kind report -h", "cci post --help | head -40"):
        assert landing_guard.deny_reason(bash(help_asked, transcript_path=str(session))) is None, help_asked
    assert landing_guard.deny_reason(bash("cci post help", transcript_path=str(session))) == landing_guard.CLI_POST
    assert landing_guard.deny_reason(bash(report, transcript_path=str(session), session_id="another-session")) is None
    cci_delta(session, "failedMcpServers", ["plugin:cc-inbox:cci"])
    assert landing_guard.deny_reason(bash(report, transcript_path=str(session))) is None
    cci_delta(session, "addedNames", ["plugin:cc-inbox:cci"])
    assert landing_guard.deny_reason(bash(report, transcript_path=str(session))) == landing_guard.CLI_POST
    cci_delta(session, "removedNames", ["mcp__plugin_cc-inbox_cci__post"])
    assert landing_guard.deny_reason(bash(report, transcript_path=str(session))) is None
    assert landing_guard.deny_reason(bash(report)) is None


def test_a_report_posted_with_cci_post_in_bash_gets_the_checks_the_cci_post_tool_runs(run: Run, demo: str, no_calls: None) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why does the search worker OOM?", "--parts", "cause", "fix")
    run("job", "add", "reader", "--order", "o1", "--part", "cause", "--goal", "find the cause")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    post = "cci post --kind report --to communicator@demo --attr job=j1 {attr} 'POST the search worker OOMs on entries past 1024 tokens'"
    reason = landing_guard.deny_reason(bash(post.format(attr="--attr progress=true")))
    assert 'o1\'s "cause" was answered by j1, which finished' in reason and '`--attr part="o1:cause"`' in reason
    assert landing_guard.deny_reason(bash(post.format(attr="--attr 'part=o1:cause'"))) is None
    assert landing_guard.deny_reason(bash(post.format(attr="--attr 'progress=true,part=o1:cause'"))) is None
    assert 'delivers "cause", "fix", the last parts of o1' in landing_guard.deny_reason(bash(post.format(attr="--attr 'part=o1:cause;o1:fix'")))
    failed = "cci post --kind report --to communicator@demo --attr 'part=o1:cause' 'POST the search worker OOMs; its card was refused since nobody checked it'"
    assert landing_guard.deny_reason(bash(failed)) is None
    assert landing_guard.unprevented_reports(bash(failed)).startswith("record_lines: this report goes out as written")


def test_each_command_reads_the_stdin_its_own_text_gives_it() -> None:
    script = "cat a.md | drive job ask j1 -\ndrive job ask j2 - < \"$d/b.md\"\ncat > c.md <<'EOF'\nc body\nEOF\ndrive job ask j3 - <<-ASK\nthe block\nASK\ngrep x <<< hi"
    assert len(shell.templates(shell.runnable(script))) == 6
    assert shell.stdins(script) == [None, Stdin("|"), Stdin("<", "$d/b.md"), Stdin("<<", "c body"), Stdin("<<", "the block"), Stdin("<<<", "hi")]


def test_a_question_kept_with_drive_job_ask_goes_to_jev_as_the_case_its_block_makes(demo: str, no_calls: None, tmp_path: Path) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    case = landing_guard.root_cause_case(bash(f"drive job ask j1 {block}", session_id=SESSION))
    assert isinstance(case, landing_guard.Asked)
    assert case.startswith("Question: How should a finished run's files be laid out")
    assert "Root cause: none: a design choice, with no defect behind it" in case
    assert landing_guard.judged_reason(bash(f"drive job ask j1 {block}", session_id=SESSION)) is None
    assert landing_guard.root_cause_case(bash(f"drive job ask --check {block}", session_id=SESSION)) is None


@pytest.mark.parametrize(
    "form",
    [
        "drive job ask j1 {block}",
        "cd {folder} && drive job ask j1 ask.md",
        "drive job ask j1 - < {block}",
        "drive job ask j1 - <<'ASK'\n{text}\nASK",
        "drive job ask j1 {block} 2>&1 | tail -5",
        "drive job ask j1 --drive demo",
    ],
)
def test_every_form_handing_the_verb_one_block_gives_jev_one_case(run: Run, demo: str, no_calls: None, tmp_path: Path, form: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["report"] = SHORT_ASK
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    command = form.format(block=block, folder=tmp_path, text=SHORT_ASK.strip())
    assert landing_guard.root_cause_case(bash(command, session_id=SESSION)) == limits.root_cause_review(SHORT_ASK, None)


@pytest.mark.parametrize(
    ("form", "why"),
    [
        ("cat {block} | drive job ask j1 -", "comes from a pipe, a here-string or the caller's stdin"),
        ("drive job ask j1 -", "comes from a pipe, a here-string or the caller's stdin"),
        ("drive job ask j1 {folder}/new.md", "new.md does not exist yet"),
        ("cp {block} {folder}/copy.md && drive job ask j1 {folder}/copy.md", "another command in the call names copy.md"),
    ],
)
def test_a_block_only_running_the_call_shows_is_refused_unjudged_for_a_file_of_its_own(demo: str, no_calls: None, tmp_path: Path, form: str, why: str) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    (tmp_path / "copy.md").write_text(SHORT_ASK)
    reason = landing_guard.root_cause_case(bash(form.format(block=block, folder=tmp_path), session_id=SESSION))
    assert isinstance(reason, agents.Unjudged)
    assert why in reason
    assert reason.endswith("Write the block to a file in a call of its own, then run `drive job ask j1 <file>` in the next call")


def test_the_hook_hands_jev_the_case_and_his_ruling_with_the_rule_for_its_root_cause_form_in_place_of_a_refusal(
    demo: str, no_calls: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash(f"drive job ask j1 - < {block}", session_id=SESSION))))
    landing_guard.main()
    case = limits.root_cause_review(SHORT_ASK, None)
    assert json.loads(capsys.readouterr().out) == {"ask": case, "his_words": [limits.ROOT_CAUSE_WORDS | {"detail": limits.ROOT_CAUSE_RULES["none"] + limits.ROOT_CAUSE_SETTLED}]}


def test_the_hook_hands_jev_the_words_each_page_the_card_links_shows_beside_his_words_on_a_link_that_opened_another_view(
    demo: str, no_calls: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    preview = "https://prototype.example.com/index.html?slack=pr"
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", f"Meanwhile nothing is built on either layout; <{preview}|the prototype's Slack preview> draws both cards."))
    monkeypatch.setattr(limits.picture, "page_text", {preview: "Priority queue Decision 1 of 72"}.__getitem__)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash(f"drive job ask j1 - < {block}", session_id=SESSION))))
    landing_guard.main()
    told = json.loads(capsys.readouterr().out)
    assert told["ask"].endswith(f"Root cause: none: a design choice, with no defect behind it\nPage {preview} shows: Priority queue Decision 1 of 72")
    assert told["his_words"] == [limits.ROOT_CAUSE_WORDS | {"detail": limits.ROOT_CAUSE_RULES["none"] + limits.ROOT_CAUSE_SETTLED}, limits.LINK_VIEW_WORDS]


@pytest.mark.parametrize("command", [".agents/skills/submit-pr/scripts/stack-enqueue 42", "gh pr merge 42 -R Forge-AI/monorepo --squash"])
def test_a_drive_session_lands_no_pr_someone_else_opened_that_no_order_of_his_names(drive_pr: str, on_github: dict, no_calls: None, command: str) -> None:
    approve(drive_pr)
    on_github["author"] = {"login": "rowan", "is_bot": False}
    assert landing_guard.deny_reason(bash(command)) == f"Refused on purpose: {state.foreign(drive_pr, 'rowan', [])}. Land, enqueue, label and rebase nothing of it"
    assert landing_guard.deny_reason(bash(command, session_id="ordinary")) is None
    on_github["author"] = {"login": "yasyf", "is_bot": False}
    assert landing_guard.deny_reason(bash(command)) == (None if "stack-enqueue" in command else landing_guard.QUEUE_REASON.format(key=drive_pr))
