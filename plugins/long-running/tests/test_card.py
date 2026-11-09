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
FIX_BODY = """## What changes for you
Uploads that fail once now retry instead of losing the file.

## ELI5
When the network hiccups, we try again.

## Risk
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
) -> dict:
    return {
        "title": title,
        "body": body,
        "state": pr_state,
        "isDraft": False,
        "headRefOid": HEAD,
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
        "title": "Fix: retry flaky uploads",
        "head": HEAD,
        "change": "Uploads that fail once now retry instead of losing the file.",
        "eli5": "When the network hiccups, we try again.",
        "show_me": "",
        "risk": "Retries could send a file twice. The server drops the copy.",
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
    body = "## Port\nBrings the upload retry from the server to the CLI.\n\n## Summary\nThe CLI retries uploads.\n"
    drawn = card.render(pr_json(title="Port: upload retry to the CLI", body=body), entry())
    assert drawn["title"] == "Port: approve upload retry to the CLI?"
    assert drawn["prs"][0]["port"] == "Brings the upload retry from the server to the CLI."
    assert drawn["prs"][0]["change"] == "The CLI retries uploads."


def test_an_exempt_port_waits_on_nothing_of_his() -> None:
    drawn = card.render(pr_json(title="Port: upload retry to the CLI", body=""), entry(exempt="pure port"))
    assert drawn == {
        "status": "info",
        "title": "Port: upload retry to the CLI",
        "prs": [
            row(
                title="Port: upload retry to the CLI",
                change="",
                eli5="",
                risk="",
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
    ],
)
def test_kind_and_title(title: str, kind: str, plain: str) -> None:
    assert card.kind_and_title(title) == (kind, plain)


def test_summary_and_risk_profile_headings_fill_change_and_risk() -> None:
    body = "## Summary\nThe status card lists open orders.\n\nA second paragraph.\n\n## Risk profile\nMedium: it rewrites the card.\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["change"] == "The status card lists open orders."
    assert first["risk"] == "Medium: it rewrites the card."


def test_a_show_me_image_becomes_a_picture() -> None:
    body = "## Show me\n![the card](https://example.com/card.png)\nThe card after his tap.\n"
    drawn = card.render(pr_json(body=body), entry())
    assert drawn["pictures"] == ["https://example.com/card.png"]
    assert drawn["prs"][0]["show_me"] == "The card after his tap."


def test_a_risk_section_saying_none_is_omitted() -> None:
    body = "## Summary\nAdds a flag.\n\n## Risk\nNone.\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["risk"] == ""


@pytest.mark.parametrize("words", ["None", "none.", "No departure", "no departure.", "N/A", "n/a."])
def test_an_against_the_plan_section_saying_none_is_omitted(words: str) -> None:
    body = f"## Summary\nAdds a flag.\n\n## Against the plan\n{words}\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["plan"] == ""


def test_long_sections_cut_at_a_sentence_end() -> None:
    sentence = "Each retry waits twice as long as the one before it. "
    body = "## Summary\n" + sentence * 10
    change = card.render(pr_json(body=body), entry())["prs"][0]["change"]
    assert change == (sentence * 7).strip()


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
    data = state.new_drive("demo", "s1", "/work", "D123")
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
    assert reply == {"cards": [{"repo": "Forge-AI/monorepo", "number": 42, "card": card.render(pr_json(), entry())}]}
    assert asked[0] == ["gh", "pr", "view", "42", "-R", "Forge-AI/monorepo", "--json", card.FIELDS]
    assert len(asked) == 2
    assert "yasyf/cc-skills#42" in capsys.readouterr().err


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
