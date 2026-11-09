import io
import json
from pathlib import Path

import pytest
from drive import card, state
from drive.github import GithubError

URL = "https://github.com/Forge-AI/monorepo/pull/42"
HEAD = "0123456789abcdef0123456789abcdef01234567"
RUNNING = [{"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS", "conclusion": ""}]
PASSING = [
    {"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"__typename": "StatusContext", "context": "buildkite", "state": "SUCCESS"},
]
FIX_BODY = """## Biggest surprise
Uploads that fail once now retry instead of losing the file.

## Refresher
When the network hiccups, we try again.

## What you'll hate most
Retries could send a file twice. The server drops the copy.

## Against the plan
The plan said three retries; this does five.
"""


def pr_json(
    title: str = "Fix: retry flaky uploads",
    body: str = FIX_BODY,
    pr_state: str = "OPEN",
    rollup: list[dict] | None = None,
    review: str = "REVIEW_REQUIRED",
    url: str = URL,
    base: str = "main",
    branch: str = "retry-uploads",
    comments: list[dict] | None = None,
) -> dict:
    return {
        "title": title,
        "body": body,
        "comments": comments or [],
        "state": pr_state,
        "isDraft": False,
        "headRefOid": HEAD,
        "baseRefName": base,
        "headRefName": branch,
        "statusCheckRollup": RUNNING if rollup is None else rollup,
        "reviewDecision": review,
        "url": url,
    }


def entry(wants_tap: bool = True, tap: str | None = None, exempt: str | None = None, pr_state: str = "open") -> dict:
    return {
        "url": URL,
        "job": "j1",
        "order": "o1",
        "wants_tap": wants_tap,
        "tap": {"label": tap, "at": "2026-11-09T08:00:00-08:00", "head": HEAD, "source": "slack"} if tap else None,
        "exempt": exempt,
        "fundamental": None,
        "state": pr_state,
        "title": "Fix: retry flaky uploads",
        "head": HEAD,
        "checks": "pending",
        "review": "none",
        "draft": False,
        "merged_sha": None,
        "updated": "2026-11-09T08:00:00-08:00",
    }


def row(**fields: str) -> dict:
    return {
        "url": URL,
        "title": "retry flaky uploads",
        "head": HEAD,
        "refresher": "When the network hiccups, we try again.",
        "surprise": "Uploads that fail once now retry instead of losing the file.",
        "show_me": "",
        "pictures": [],
        "hate_most": "Retries could send a file twice. The server drops the copy.",
        "plan": "The plan said three retries; this does five.",
        "port": "",
        "blocks": "",
        "lands": "",
        "description": FIX_BODY.strip(),
        **fields,
    }


def test_a_monorepo_fix_waiting_on_his_tap_asks_for_approve() -> None:
    assert card.render(pr_json(), entry()) == {
        "status": "needs_you",
        "title": "Fix: approve retry flaky uploads?",
        "prs": [row(lands="you approve and CI passes")],
        "options": ["Approve", "Hold"],
        "recommended": "Approve",
        "waits": "merge:Forge-AI:monorepo:42",
    }


def test_after_his_approve_while_ci_runs_it_drops_the_buttons() -> None:
    assert card.render(pr_json(), entry(tap="Approve")) == {
        "status": "info",
        "title": "Fix: retry flaky uploads",
        "prs": [row(blocks="Lands once CI passes")],
    }


def test_a_merged_pr_is_done() -> None:
    assert card.render(pr_json(pr_state="MERGED", rollup=PASSING), entry(tap="Approve")) == {
        "status": "done",
        "title": "Fix: retry flaky uploads",
        "prs": [row(blocks="Merged")],
    }


def test_a_closed_pr_says_it_closed_without_merging() -> None:
    assert card.render(pr_json(pr_state="CLOSED"), entry()) == {
        "status": "info",
        "title": "Fix: retry flaky uploads",
        "prs": [row(blocks="Closed without merging")],
    }


def test_a_held_pr_keeps_its_buttons_and_says_held() -> None:
    assert card.render(pr_json(rollup=PASSING), entry(tap="Hold")) == {
        "status": "needs_you",
        "title": "Fix: approve retry flaky uploads?",
        "prs": [row(blocks="Held by you", lands="you approve")],
        "options": ["Approve", "Hold"],
        "recommended": "Approve",
        "waits": "merge:Forge-AI:monorepo:42",
    }


def test_a_port_carries_its_port_section() -> None:
    body = "## Port\nBrings the upload retry from the server to the CLI.\n\n## Biggest surprise\nThe CLI retries uploads.\n"
    drawn = card.render(pr_json(title="Port: upload retry to the CLI", body=body), entry())
    assert drawn["title"] == "Port: approve upload retry to the CLI?"
    assert drawn["prs"][0]["port"] == "Brings the upload retry from the server to the CLI."
    assert drawn["prs"][0]["surprise"] == "The CLI retries uploads."


def test_an_exempt_port_waits_on_nothing_of_his() -> None:
    drawn = card.render(pr_json(title="Port: upload retry to the CLI", body=""), entry(exempt="pure port"))
    assert drawn == {
        "status": "info",
        "title": "Port: upload retry to the CLI",
        "prs": [
            row(
                title="upload retry to the CLI",
                surprise="",
                refresher="",
                hate_most="",
                plan="",
                description="",
                blocks="CI is still running",
            )
        ],
    }


def test_a_conventional_commit_title_becomes_its_kind_and_plain_words() -> None:
    drawn = card.render(pr_json(title="fix(api): ✨ thing"), entry())
    assert drawn["title"] == "Fix: approve thing?"


@pytest.mark.parametrize(
    ("title", "kind", "plain"),
    [
        ("fix(api): ✨ thing", "Fix", "thing"),
        ("✨ feat!: add the `--due` flag", "New", "add the `--due` flag"),
        ("chore(deps): bump pytest", "Cleanup", "bump pytest"),
        ("docs: explain taps", "Cleanup", "explain taps"),
        ("New: \U0001f680 rollup card", "New", "rollup card"),
        ("Cleanup: drop the board", "Cleanup", "drop the board"),
        ("style: reflow", "Change", "reflow"),
        ("Bump the plugin", "Change", "Bump the plugin"),
        ("sandsql-server: ✨ a moved table commits in both files", "New", "a moved table commits in both files"),
        ("ci: 🐛 pin smoke candidates once", "Fix", "pin smoke candidates once"),
        ("api: 🔥 drop the old table", "Cleanup", "drop the old table"),
        ("long-running: ⚡️ the watch reads once", "Cleanup", "the watch reads once"),
        ("go/sand: 🎨 tidy the loop", "Change", "tidy the loop"),
    ],
)
def test_kind_and_title(title: str, kind: str, plain: str) -> None:
    assert card.kind_and_title(title) == (kind, plain)


def test_a_summary_is_no_surprise_and_risk_stays_off_the_card() -> None:
    body = "## Summary\nThe status card lists open orders.\n\nA second paragraph.\n\n## Risk profile\n- **Threat model:** internal.\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["surprise"] == ""
    assert "risk" not in first
    assert "Threat model" not in json.dumps({key: value for key, value in first.items() if key != "description"})


@pytest.mark.parametrize("heading", ["What you'll hate most", "What you\u2019ll hate the most", "What you will hate most"])
def test_what_he_will_hate_most_fills_its_line(heading: str) -> None:
    body = f"## Summary\nAdds a flag.\n\n## {heading}\nThe flag is on for every team at once.\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["hate_most"] == "The flag is on for every team at once."


def test_drawn_pictures_ride_the_card_and_the_show_me_keeps_one_caption() -> None:
    body = "## Show me\n![the card](https://example.com/card.png)\nThe card after his tap. It has buttons.\n"
    drawn = card.render(pr_json(body=body), entry(), ("/pictures/app PR 42.png",))
    assert drawn["pictures"] == drawn["prs"][0]["pictures"] == ["/pictures/app PR 42.png"]
    assert drawn["prs"][0]["show_me"] == "The card after his tap."


def test_closing_git_trailers_never_reach_the_card() -> None:
    body = FIX_BODY + "\n## Show me\n\n![shot](https://example.com/shot.png)\n\nRelease-With: api dashboard\r\nCo-authored-by: A <a@b.c>\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["show_me"] == ""
    assert "Release-With" not in first["description"]


def test_a_show_me_without_a_picture_shows_its_code_block() -> None:
    body = "## Show me\nThe new call and its answer.\n\n```sand\nrun_journal(run)\n# => 12 steps\n```\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["show_me"] == "The new call and its answer.\n```\nrun_journal(run)\n# => 12 steps\n```"


def test_a_show_me_of_internals_prose_shows_nothing() -> None:
    body = """## Show me
The network-readiness invariant has one owner, `cdpBrowser.trackNetwork`: the in-flight set and the lifecycle events describe the same requests.

The earlier #49744 fixes a separate invariant. Release relayed the raw folds in cci 638067, answering probe request 3566. Disabling PostHog was rejected in 637450.

| warning | count |
|---|---|
| settle | 18 |
</details>
"""
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["show_me"] == ""


def test_record_ids_never_reach_a_card_line() -> None:
    body = (
        "## Biggest surprise\nUploads retry (cci 638067). A failed upload no longer loses the file.\n\n"
        "## Refresher\nWe try again. Root ruled it in 646598 for j134, per cc-notes task d0545d2.\n\n"
        "## Against the plan\nThe plan said three retries (card rmw8x1pwx); this does five. See m2074.\n"
    )
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["surprise"] == "Uploads retry. A failed upload no longer loses the file."
    assert first["refresher"] == "We try again."
    assert first["plan"] == "The plan said three retries; this does five."


def test_a_heading_inside_a_code_fence_stays_in_its_section() -> None:
    body = "## Show me\nA shell session.\n\n```sh\n# list the drives\ndrive status\n```\n\n## ELI5\nIt lists drives.\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["show_me"] == "A shell session.\n```\n# list the drives\ndrive status\n```"
    assert first["refresher"] == "It lists drives."


def test_the_refresher_comes_from_a_reviewer_comment_when_the_body_has_none() -> None:
    comments = [
        {"author": {"login": "someone"}, "body": "Looks good."},
        {"author": {"login": "forge-pr-reviewer"}, "body": "<!-- companion -->\n\n### ELI5\n\nUploads try again.\n\n### Diagram\nflow"},
    ]
    first = card.render(pr_json(body="## Summary\nAdds retries.\n", comments=comments), entry())["prs"][0]
    assert first["refresher"] == "Uploads try again."


@pytest.mark.parametrize("words", ["None.", "Nothing.", "Nothing differed."])
def test_a_hate_or_plan_section_saying_nothing_is_omitted(words: str) -> None:
    body = f"## Summary\nAdds a flag.\n\n## What you'll hate most\n{words}\n\n## Against the plan\n{words}\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert (first["hate_most"], first["plan"]) == ("", "")


@pytest.mark.parametrize("words", ["None", "none.", "No departure", "no departure.", "N/A", "n/a."])
def test_an_against_the_plan_section_saying_none_is_omitted(words: str) -> None:
    body = f"## Summary\nAdds a flag.\n\n## Against the plan\n{words}\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["plan"] == ""


def test_long_sections_cut_at_a_sentence_end() -> None:
    sentence = "Each retry waits twice as long as the one before it. "
    body = "## Refresher\n" + sentence * 10
    refresher = card.render(pr_json(body=body), entry())["prs"][0]["refresher"]
    assert refresher == (sentence * 7).strip()


def test_template_comments_stay_out_of_the_card() -> None:
    body = "<!-- say what changes -->\n## Summary\nAdds a flag.\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["description"] == "## Summary\nAdds a flag."


def test_failed_ci_names_the_agent_fixing_it() -> None:
    failing = [{"__typename": "StatusContext", "context": "buildkite", "state": "FAILURE"}]
    blocks = card.render(pr_json(rollup=failing), entry(wants_tap=False))["prs"][0]["blocks"]
    assert blocks == "CI failed; the agent is fixing it"


@pytest.fixture
def drive(state_root: Path) -> str:
    data = state.new_drive("demo", "coordinator", "s1", "/work", "D123")
    data["prs"] = {
        "Forge-AI/monorepo#42": entry(),
        "yasyf/cc-skills#42": {**entry(wants_tap=False), "url": "https://github.com/yasyf/cc-skills/pull/42"},
        "Forge-AI/monorepo#7": {**entry(pr_state="merged"), "url": "https://github.com/Forge-AI/monorepo/pull/7"},
    }
    state.create(data)
    return "demo"


def ask(request: dict) -> dict:
    out = io.StringIO()
    assert card.main(io.StringIO(json.dumps(request)), out) == 0
    return json.loads(out.getvalue())


def events(name: str) -> list[dict]:
    return [event for event, _ in state.read_events(name)]


def test_prs_draws_each_known_pr_and_omits_the_rest(
    drive: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[list[str]] = []

    def gh(cmd: list[str]) -> str:
        asked.append(cmd)
        if cmd[5] == "yasyf/cc-skills":
            raise GithubError("GraphQL: Could not resolve to a PullRequest")
        return json.dumps(pr_json())

    monkeypatch.setattr(card, "run", gh)
    reply = ask(
        {
            "prs": [
                {"repo": "Forge-AI/monorepo", "number": 42},
                {"repo": "yasyf/cc-skills", "number": 42},
                {"repo": "Forge-AI/monorepo", "number": 99},
            ]
        }
    )
    assert reply == {
        "cards": [{"repo": "Forge-AI/monorepo", "number": 42, "card": card.render(pr_json(), entry())}],
        "skipped": [{"repo": "yasyf/cc-skills", "number": 42, "reason": "drive card could not read it from GitHub"}],
    }
    assert asked[0] == ["gh", "pr", "view", "42", "-R", "Forge-AI/monorepo", "--json", card.FIELDS]
    assert len(asked) == 2
    err = capsys.readouterr().err
    assert "yasyf/cc-skills#42" in err
    assert "Forge-AI/monorepo#99 is in no drive" in err


def test_prs_matches_the_lowercase_repo_cc_slack_asks_with(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", lambda cmd: json.dumps(pr_json()))
    reply = ask({"prs": [{"repo": "forge-ai/monorepo", "number": 42}]})
    assert [(drawn["repo"], drawn["number"]) for drawn in reply["cards"]] == [("Forge-AI/monorepo", 42)]


def test_a_tap_card_without_what_he_will_hate_most_is_skipped_and_refused(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## What you'll hate most")[0]
    monkeypatch.setattr(card, "run", lambda cmd: json.dumps(pr_json(body=body)))
    reason = f"its tap card needs a \"What you'll hate most\" section in the body of {URL}"
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]}) == {
        "cards": [],
        "skipped": [{"repo": "Forge-AI/monorepo", "number": 42, "reason": reason}],
    }
    assert [(event["kind"], event["reason"]) for event in events(drive)] == [("refused", reason)]


def test_a_card_that_waits_on_no_tap_draws_without_what_he_will_hate_most(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## What you'll hate most")[0]
    monkeypatch.setattr(card, "run", lambda cmd: json.dumps(pr_json(body=body, pr_state="MERGED")))
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert drawn["card"]["status"] == "done"


def test_numbers_lists_every_drive_pr_with_that_number(drive: str) -> None:
    assert ask({"numbers": [42]}) == {
        "prs": [{"repo": "Forge-AI/monorepo", "number": 42}, {"repo": "yasyf/cc-skills", "number": 42}]
    }


def test_shown_counts_known_prs_and_wakes_nobody(drive: str) -> None:
    shown = [
        {"repo": "Forge-AI/monorepo", "number": 42, "waits": "merge:Forge-AI:monorepo:42"},
        {"repo": "Forge-AI/monorepo", "number": 99, "waits": ""},
    ]
    assert ask({"shown": shown}) == {"shown": 1}
    assert ask({"shown": shown}) == {"shown": 1}
    assert events(drive) == []


def test_refused_appends_one_event_per_new_reason(drive: str) -> None:
    refused = [{"repo": "Forge-AI/monorepo", "number": 42, "reason": "card needs a title"}]
    assert ask({"refused": refused}) == {"refused": 1}
    assert ask({"refused": refused}) == {"refused": 0}
    assert ask({"refused": [{**refused[0], "reason": "pull request is merged"}]}) == {"refused": 1}
    assert [(event["kind"], event["reason"]) for event in events(drive)] == [
        ("refused", "card needs a title"),
        ("refused", "pull request is merged"),
    ]


def test_answer_stores_his_tap_and_appends_a_tap_event(drive: str) -> None:
    answer = {
        "repo": "Forge-AI/monorepo",
        "number": 42,
        "waits": "merge:Forge-AI:monorepo:42",
        "label": "Approve",
        "action_ts": "1762700000.000100",
        "source": "slack",
    }
    assert ask({"answer": answer}) == {"ok": True}
    tap = state.load(drive)["prs"]["Forge-AI/monorepo#42"]["tap"]
    assert (tap["label"], tap["head"], tap["source"]) == ("Approve", HEAD, "slack")
    [event] = events(drive)
    assert {key: event[key] for key in ("kind", "repo", "number", "label")} == {
        "kind": "tap",
        "repo": "Forge-AI/monorepo",
        "number": 42,
        "label": "Approve",
    }


def test_answer_matches_the_lowercase_repo_cc_slack_sends(drive: str) -> None:
    answer = {"repo": "forge-ai/monorepo", "number": 42, "waits": "", "label": "Hold", "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert state.load(drive)["prs"]["Forge-AI/monorepo#42"]["tap"]["label"] == "Hold"


def test_answer_on_a_merged_pr_stores_nothing(drive: str) -> None:
    answer = {"repo": "Forge-AI/monorepo", "number": 7, "waits": "", "label": "Approve", "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert state.load(drive)["prs"]["Forge-AI/monorepo#7"]["tap"] is None
    assert events(drive) == []


def test_answer_on_an_unknown_pr_fails(drive: str) -> None:
    answer = {"repo": "o/n", "number": 1, "waits": "", "label": "Approve", "action_ts": "", "source": "s"}
    with pytest.raises(state.DriveError, match="not a drive PR"):
        ask({"answer": answer})


def test_unheard_appends_an_event(drive: str) -> None:
    unheard = {
        "card": "1762700000.000200",
        "label": "Other",
        "question": "Which drive first?",
        "other": True,
        "permalink": "https://forge.slack.com/archives/D123/p1762700000000200",
        "key": "q1",
    }
    assert ask({"unheard": unheard}) == {"heard": True}
    [event] = events(drive)
    assert event["kind"] == "unheard"
    assert event["question"] == "Which drive first?"


STACK_BODY = "## The stack\nthe orders table moves to a new one without a pause\n\nReaders read both tables, every row is copied across, then the old table goes.\n\n"
STACK = {
    41: ("New: readers read both tables", "main", "move-1"),
    42: ("New: copy every row across", "move-1", "move-2"),
    43: ("Cleanup: drop the old table", "move-2", "move-3"),
}


def stack_url(number: int) -> str:
    return f"https://github.com/Forge-AI/monorepo/pull/{number}"


@pytest.fixture
def stacked(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    data = state.new_drive("demo", "coordinator", "s1", "/work", "D123")
    data["prs"] = {f"Forge-AI/monorepo#{number}": {**entry(), "url": stack_url(number)} for number in (41, 42, 43, 44)}
    state.create(data)
    asked: list[list[str]] = []

    def gh(cmd: list[str]) -> str:
        asked.append(cmd)
        number = int(cmd[3])
        title, base, branch = STACK.get(number, ("Fix: a later fix on top", "move-3", "move-4"))
        body = STACK_BODY + FIX_BODY if number == 41 else FIX_BODY
        return json.dumps(pr_json(title=title, body=body, url=stack_url(number), base=base, branch=branch))

    monkeypatch.setattr(card, "run", gh)
    return asked


def asks(*numbers: int) -> dict:
    return {"prs": [{"repo": "Forge-AI/monorepo", "number": number} for number in numbers]}


def test_a_stack_draws_one_card_listing_each_pr_bottom_first(stacked: list[list[str]]) -> None:
    [drawn] = ask(asks(43, 41, 42))["cards"]
    assert (drawn["repo"], drawn["number"]) == ("Forge-AI/monorepo", 41)
    stack_card = drawn["card"]
    assert [row["url"] for row in stack_card["prs"]] == [stack_url(41), stack_url(42), stack_url(43)]
    assert stack_card["title"] == "New: the orders table moves to a new one without a pause"
    assert stack_card["body"] == "Readers read both tables, every row is copied across, then the old table goes."
    assert stack_card["waits"] == "merge:Forge-AI:monorepo:41-43"
    assert (stack_card["options"], stack_card["recommended"], stack_card["status"]) == (["Approve", "Hold"], "Approve", "needs_you")
    assert all(row["hate_most"] == "Retries could send a file twice. The server drops the copy." for row in stack_card["prs"])
    keys = ["Forge-AI/monorepo#41", "Forge-AI/monorepo#42", "Forge-AI/monorepo#43"]
    assert all(state.load("demo")["prs"][key]["stack"] == keys for key in keys)


def test_a_stack_card_waiting_on_him_needs_the_stack_section(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "STACK_HEADING", "no such section")
    drawn = ask(asks(41, 42, 43))
    assert drawn["cards"] == []
    assert all("The stack" in skipped["reason"] for skipped in drawn["skipped"])


def test_a_redraw_of_one_pr_in_a_stack_redraws_the_whole_stack(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    [drawn] = ask(asks(42))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(41), stack_url(42), stack_url(43)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:41-43"


def test_a_pr_added_to_the_stack_later_gets_its_own_card(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    [drawn] = ask(asks(44))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(44)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:44"
    assert "stack" not in state.load("demo")["prs"]["Forge-AI/monorepo#44"]


def test_approve_on_a_stack_card_taps_each_open_pr_bottom_first(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#42"]["state"] = "merged"
    answer = {
        "repo": "Forge-AI/monorepo",
        "number": 41,
        "waits": "merge:Forge-AI:monorepo:41-43",
        "label": "Approve",
        "action_ts": "1762700000.000100",
        "source": "slack",
    }
    assert ask({"answer": answer}) == {"ok": True}
    prs = state.load("demo")["prs"]
    assert [(prs[f"Forge-AI/monorepo#{n}"]["tap"] or {}).get("label") for n in (41, 42, 43)] == ["Approve", None, "Approve"]
    assert [(event["kind"], event["number"], event["label"]) for event in events("demo")] == [
        ("tap", 41, "Approve"),
        ("tap", 43, "Approve"),
    ]


def test_a_tap_on_a_single_pr_card_taps_only_that_pr(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    answer = {"repo": "Forge-AI/monorepo", "number": 41, "waits": "merge:Forge-AI:monorepo:41", "label": "Hold", "action_ts": "", "source": "s"}
    ask({"answer": answer})
    assert [event["number"] for event in events("demo")] == [41]
