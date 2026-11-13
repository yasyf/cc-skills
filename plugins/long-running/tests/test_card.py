import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Chrome, Jev, Judge, Redraws
from drive import card, cli, github, picture, stage, state, superseded, view
from drive.github import GithubError

URL = "https://github.com/Forge-AI/monorepo/pull/42"
HEAD = "0123456789abcdef0123456789abcdef01234567"
MOVED = "fedcba9876543210fedcba9876543210fedcba98"
HIS_TAPS = "https://acme.slack.com/archives/D123/p1794515484282799"
SUMMARY = "`upload.Retry` retries a failed upload up to five times instead of dropping the file."
FIX_BODY = f"""## Summary
{SUMMARY}

## Biggest surprise
Uploads that fail once now retry instead of losing the file.

## Refresher
When the network hiccups, we try again.

## What you'll hate most
Retries could send a file twice. The server drops the copy.

## Against the plan
The plan said three retries; this does five.

## His words, step by step
- "uploads should survive a flaky network" beside `upload.go:42`
"""
def pr_json(
    title: str = "Fix: retry flaky uploads",
    body: str = FIX_BODY,
    pr_state: str = "OPEN",
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
        "reviewDecision": review,
        "labels": [],
        "url": url,
    }


def answering(pr: dict) -> Callable[[list[str]], str]:
    """A stand-in for gh answering card.read: the PR without its comments for gh pr view, and its comments for the REST read."""
    return lambda cmd, **_: json.dumps(pr["comments"] if cmd[1] == "api" else {field: value for field, value in pr.items() if field != "comments"})


def entry(wants_tap: bool = True, tap: str | None = None, exempt: str | None = None, pr_state: str = "open", checks: str = "pending", now: str | None = None) -> dict:
    named = now or default_stage(wants_tap, tap, exempt, pr_state, checks)
    return {
        "url": URL,
        "job": "j1",
        "order": "o1",
        "wants_tap": wants_tap,
        "tap": {"label": tap, "at": "2026-11-09T08:00:00-08:00", "head": HEAD, "source": "slack"} if tap else None,
        "exempt": exempt,
        "fundamental": None,
        "rework": None,
        "state": pr_state,
        "title": "Fix: retry flaky uploads",
        "head": HEAD,
        "checks": checks,
        "failed_checks": [],
        "review": "none",
        "draft": False,
        "hold_label": False,
        "conflict": False,
        "merged_sha": None,
        "checked": {"head": HEAD, "read": HEAD, "orders": ["o1"], "lines": {}},
        "checker": None,
        "updated": "2026-11-09T08:00:00-08:00",
        "now": stage.Stage(named, "", ""),
    }


def kept_entry(**fields: object) -> dict:
    """A PR's row as drive.json keeps it: its entry with no stage, which card.render's callers give it from stage.of."""
    return {key: value for key, value in entry(**fields).items() if key != "now"}


def default_stage(wants_tap: bool, tap: str | None, exempt: str | None, pr_state: str, checks: str) -> str:
    if pr_state != "open":
        return pr_state
    if tap == "Hold":
        return "held"
    if checks != "pass":
        return "red" if checks == "fail" else "grading"
    if exempt or tap == "Approve":
        return "ready"
    return "card" if wants_tap else "author"


def judged_job(data: dict) -> dict:
    data["orders"]["o1"] = state.new_order("ship the run page", "terminal", None, {}, "2026-11-09T08:00:00-08:00") | {"jobs": ["j1"], "project": "Iris", "workstream": "Run page"}
    data["jobs"]["j1"] = state.new_job("o1", "builder", "ship", [], "2026-11-09T08:00:00-08:00")
    return data


def row(**fields: str) -> dict:
    return {
        "url": URL,
        "title": "retry flaky uploads",
        "head": HEAD,
        "changed": "",
        "review": "",
        "summary": SUMMARY,
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


def test_a_change_since_his_tap_asks_again_with_what_changed() -> None:
    changed = {**entry(), "fundamental": "rewritten to do the opposite of the version he held"}
    drawn = card.render(pr_json(), changed)
    assert (drawn["status"], drawn["waits"], drawn["prs"][0]["changed"]) == (
        "needs_you",
        "merge:Forge-AI:monorepo:42",
        "rewritten to do the opposite of the version he held",
    )


def test_after_his_approve_while_ci_runs_it_drops_the_buttons_and_says_he_approved_it() -> None:
    assert card.render(pr_json(), entry(tap="Approve")) == {
        "status": "info",
        "title": "Fix: retry flaky uploads",
        "prs": [row(blocks="Approved by you. Lands once CI passes")],
    }


def test_a_merged_pr_is_done() -> None:
    assert card.render(pr_json(pr_state="MERGED"), entry(tap="Approve")) == {
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


def test_checks_the_watch_read_on_another_head_read_as_still_running_and_an_approved_green_monorepo_pr_reads_as_queued_by_the_drive() -> None:
    passed = entry(tap="Approve", checks="pass")
    assert card.render(pr_json(), passed)["prs"][0]["blocks"] == "Approved by you. CI passed; the drive queues it for merge"
    assert card.render(pr_json(review="APPROVED"), entry(exempt="test-only", checks="pass"))["prs"][0]["blocks"] == "CI passed; the drive queues it for merge"
    assert card.render(pr_json(), {**passed, "head": "f" * 40, "now": stage.Stage("grading", "", "")})["prs"][0]["blocks"] == "Approved by you. Lands once CI passes"
    other = pr_json(url="https://github.com/Forge-AI/design-docs/pull/42")
    assert card.render(other, {**passed, "url": other["url"]})["prs"][0]["blocks"] == "Approved by you. CI passed; the agent is landing it"


def test_a_held_pr_drops_its_buttons_and_says_held_by_you() -> None:
    assert card.render(pr_json(), entry(tap="Hold", checks="pass")) == {
        "status": "info",
        "title": "Fix: retry flaky uploads",
        "prs": [row(blocks="Held by you")],
    }


def test_a_pr_the_drive_holds_with_the_hold_label_asks_him_nothing() -> None:
    labeled = pr_json() | {"labels": [{"id": "LA_1", "name": "hold", "description": "", "color": "d93f0b"}]}
    assert card.render(labeled, entry(now="held") | {"hold_label": True}) == {
        "status": "info",
        "title": "Fix: retry flaky uploads",
        "prs": [row(blocks="Held by the drive with the hold label on GitHub")],
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
                summary="",
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
        ("go/sand: 🎨 tidy the loop", "Cleanup", "tidy the loop"),
        ("api: \U0001f5c3\ufe0f a pass deletes two Envs", "New", "a pass deletes two Envs"),
        ("api: \U0001f5c3 a pass deletes two Envs", "New", "a pass deletes two Envs"),
        ("api: \U0001f5c3\ufe0f Fix: a pass deletes two Envs", "Fix", "a pass deletes two Envs"),
        ("iris: \u270f\ufe0f the composer's label", "Fix", "the composer's label"),
        ("tools: \U0001f9d1\u200d\U0001f4bb one command starts Tilt", "Cleanup", "one command starts Tilt"),
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
    assert first["show_me"] == "The new call and its answer.\n\n```\nrun_journal(run)\n# => 12 steps\n```"


def test_a_show_me_of_internals_prose_shows_only_its_table() -> None:
    body = """## Show me
The network-readiness invariant has one owner, `cdpBrowser.trackNetwork`: the in-flight set and the lifecycle events describe the same requests.

The earlier #49744 fixes a separate invariant. Release relayed the raw folds in cci 638067, answering probe request 3566. Disabling PostHog was rejected in 637450.

| warning | count |
|---|---|
| settle | 18 |
</details>
"""
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["show_me"] == "| warning | count |\n|---|---|\n| settle | 18 |"


CHART = '{"mark": "bar", "data": {"values": [{"step": "before", "seconds": 40}, {"step": "after", "seconds": 2}]}, "encoding": {"x": {"field": "step", "type": "nominal"}, "y": {"field": "seconds", "type": "quantitative"}}}'
NATIVE_SHOW_ME = f"""## Show me
Each wake, before and after.

```chart
{CHART}
```

| wake | seconds |
|---|---|
| before | 40 (measured) |

```mermaid
flowchart TD
  wake --> read
```
"""


def test_a_pr_show_mes_tables_and_charts_slack_draws_reach_the_card_as_written_beside_its_caption() -> None:
    first = card.render(pr_json(body=NATIVE_SHOW_ME), entry(), ("/pictures/abc/Diagram",))["prs"][0]
    assert first["show_me"] == f"Each wake, before and after.\n\n```chart\n{CHART}\n```\n\n| wake | seconds |\n|---|---|\n| before | 40 (measured) |"
    undrawable = NATIVE_SHOW_ME.replace('"before"', '"before the queue drains"')
    assert card.render(pr_json(body=undrawable), entry())["prs"][0]["show_me"] == "Each wake, before and after.\n\n| wake | seconds |\n|---|---|\n| before | 40 (measured) |"


def test_two_show_me_tables_reach_the_card_as_two_tables_with_a_blank_line_between() -> None:
    body = "## Show me\nEach restore, timed and sized.\n\n| run | seconds |\n|---|---|\n| r1 | 4.2 |\n\n| run | bytes |\n|---|---|\n| r1 | 3 MiB |\n"
    first = card.render(pr_json(body=body), entry())["prs"][0]
    assert first["show_me"] == "Each restore, timed and sized.\n\n| run | seconds |\n|---|---|\n| r1 | 4.2 |\n\n| run | bytes |\n|---|---|\n| r1 | 3 MiB |"


def test_a_stack_cards_charts_stop_at_the_two_one_slack_message_draws_the_lowest_prs_first() -> None:
    show_me = f"## Show me\nEach wake, before and after.\n\n```chart\n{CHART}\n```\n\n```chart\n{CHART}\n```\n"
    members = [(pr_json(url=stack_url(number), body=show_me), {**entry(), "url": stack_url(number)}, (), ()) for number in (41, 42)]
    assert [row["show_me"] for row in card.drawn_card(members)["prs"]] == [
        f"Each wake, before and after.\n\n```chart\n{CHART}\n```\n\n```chart\n{CHART}\n```",
        "Each wake, before and after.",
    ]


ROWS = '[{"step": "before", "seconds": 40}, {"step": "after", "seconds": 2}]'


@pytest.mark.parametrize(
    "chart",
    [
        CHART.replace(ROWS, "[1]"),
        CHART.replace('"encoding": {', '"encoding": {"color": "step", '),
        CHART.replace('"seconds": 40', '"seconds": "40"'),
        CHART.replace('"after"', '"before"'),
    ],
)
def test_a_pr_show_me_chart_whose_rows_slack_cannot_draw_leaves_the_card(chart: str) -> None:
    body = f"## Show me\nEach wake, before and after.\n\n```chart\n{chart}\n```\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["show_me"] == "Each wake, before and after."


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
    assert first["show_me"] == "A shell session.\n\n```\n# list the drives\ndrive status\n```"
    assert first["refresher"] == "It lists drives."


def test_a_monorepo_context_is_the_refresher() -> None:
    body = "## Context\nThe uploader writes each file once.\n\n## Summary\nAdds retries.\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["refresher"] == "The uploader writes each file once."


def test_the_refresher_comes_from_a_reviewer_comment_when_the_body_has_none() -> None:
    comments = [
        {"author": {"login": "someone"}, "body": "Looks good."},
        {"author": {"login": "forge-pr-reviewer"}, "body": "<!-- companion -->\n\n### ELI5\n\nUploads try again.\n\n### Diagram\nflow"},
    ]
    first = card.render(pr_json(body="## Summary\nAdds retries.\n", comments=comments), entry())["prs"][0]
    assert first["refresher"] == "Uploads try again."


def test_a_card_read_asks_gh_pr_view_for_no_field_only_his_token_answers_and_takes_comments_over_rest(
    drive: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[list[str]] = []
    comments = [{"user": {"login": "forge-pr-reviewer[bot]"}, "body": "### Refresher\n\nUploads try again."}]
    served = answering(pr_json(body=FIX_BODY.replace("## Refresher\nWhen the network hiccups, we try again.\n\n", ""), comments=comments))

    def gh(cmd: list[str], **_: str) -> str:
        asked.append(cmd)
        return served(cmd)

    monkeypatch.setattr(card, "run", gh)
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert drawn["card"]["prs"][0]["refresher"] == "Uploads try again."
    viewed = [cmd[-1].split(",") for cmd in asked if cmd[1:3] == ["pr", "view"]]
    assert viewed and not {"comments", "reactionGroups", "reviewRequests"} & {field for fields in viewed for field in fields}


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


def test_angle_brackets_and_quotes_in_a_pr_title_and_body_reach_the_card_while_github_html_tags_do_not() -> None:
    body = '## Summary\n`agent::of("<id>")` now returns the agent, and agent::of("<id>") <b>never</b> returns "".\n'
    drawn = card.render(pr_json(title='Fix: agent::of("<id>") returns the agent', body=body), entry())
    assert (drawn["title"], drawn["prs"][0]["summary"]) == (
        'Fix: approve agent::of("<id>") returns the agent?',
        '`agent::of("<id>")` now returns the agent, and agent::of("<id>") never returns "".',
    )


def test_failed_ci_names_the_agent_fixing_it() -> None:
    blocks = card.render(pr_json(), entry(wants_tap=False, checks="fail"))["prs"][0]["blocks"]
    assert blocks == "CI failed; the agent is fixing it"


@pytest.fixture
def drive(state_root: Path) -> str:
    data = judged_job(state.new_drive("demo", "coordinator", "s1", "/work", "D123"))
    data["prs"] = {
        "Forge-AI/monorepo#42": kept_entry(),
        "yasyf/cc-skills#42": {**kept_entry(wants_tap=False), "url": "https://github.com/yasyf/cc-skills/pull/42"},
        "Forge-AI/monorepo#7": {**kept_entry(pr_state="merged"), "url": "https://github.com/Forge-AI/monorepo/pull/7"},
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

    def gh(cmd: list[str], **_: str) -> str:
        asked.append(cmd)
        if "yasyf/cc-skills" in cmd:
            raise GithubError("GraphQL: Could not resolve to a PullRequest")
        return answering(pr_json())(cmd)

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
    untapped = "its author or its own builder lands it with no tap, so no PR card draws, since it would offer him no Approve"
    assert reply == {
        "cards": [{"repo": "Forge-AI/monorepo", "number": 42, "card": card.render(pr_json(), entry())}],
        "skipped": [{"repo": "yasyf/cc-skills", "number": 42, "reason": untapped}],
    }
    assert asked == [
        ["gh", "pr", "view", "42", "-R", "Forge-AI/monorepo", "--json", card.FIELDS],
        ["gh", "api", "--paginate", "repos/Forge-AI/monorepo/issues/42/comments?per_page=100"],
        ["gh", "pr", "view", "42", "-R", "yasyf/cc-skills", "--json", card.FIELDS],
    ]
    err = capsys.readouterr().err
    assert "yasyf/cc-skills#42" in err
    assert "Forge-AI/monorepo#99 is in no drive" in err


def test_a_card_github_refuses_draws_from_its_last_read_with_the_state_the_drive_holds(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json()))
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]

    def refused(cmd: list[str], **_: str) -> str:
        raise GithubError("ccx: GitHub rate limit floor: app graphql 0/10000 left")

    monkeypatch.setattr(card, "run", refused)
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["state"] = "merged"
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}], "redraw": True})["cards"]
    assert (drawn["card"]["status"], drawn["card"]["prs"][0]["blocks"], drawn["card"]["prs"][0]["summary"]) == ("done", "Merged", SUMMARY)


def test_a_card_github_refuses_for_a_pr_no_card_read_draws_from_its_drive_entry_alone(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(cmd: list[str], **_: str) -> str:
        raise GithubError("ccx: GitHub rate limit floor: app graphql 0/10000 left")

    monkeypatch.setattr(card, "run", refused)
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["review"] = "approved"
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    shown = (drawn["card"]["status"], drawn["card"]["title"], drawn["card"]["options"], drawn["card"]["prs"][0]["summary"])
    assert shown == ("needs_you", "Fix: approve retry flaky uploads?", ["Approve", "Hold"], "")


def test_no_card_is_drawn_for_a_pr_someone_else_opened_unless_the_order_it_serves_names_it(
    drive: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json()))
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "retry flaky uploads")
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["author"] = "rowan"
    reply = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})
    assert (reply["cards"], [skipped["reason"] for skipped in reply["skipped"]]) == ([], [state.foreign("Forge-AI/monorepo#42", "rowan", [])])
    with state.edit(drive) as data:
        data["orders"]["o1"]["text"] = "land #42 once it is green"
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]


def test_prs_matches_the_lowercase_repo_cc_slack_asks_with(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json()))
    reply = ask({"prs": [{"repo": "forge-ai/monorepo", "number": 42}]})
    assert [(drawn["repo"], drawn["number"]) for drawn in reply["cards"]] == [("Forge-AI/monorepo", 42)]


def test_a_pr_waiting_on_his_open_design_card_draws_its_card_with_approve_saying_it_lands_once_he_answers(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json()))
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["waits"] = ["demo:j1-7-a"]
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert drawn["card"]["options"] == ["Approve", "Hold"]
    assert drawn["card"]["prs"][0]["lands"].startswith("you approve") and "you answer" in drawn["card"]["prs"][0]["lands"]


def test_a_pr_whose_wait_holds_redraws_his_open_card_with_its_buttons_saying_what_it_waits_on(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", lambda cmd, **_: json.dumps(pr_json()))
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["waits"] = ["release:dashboard:Forge-AI/monorepo#7"]
    tap_card = {"url": "https://acme.slack.com/archives/D123/p1794029589141509", "prs": [URL], "asked": "2026-11-09T08:00:00-08:00", "card": {"title": "Fix: approve retry flaky uploads?"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap_card], "closed": []})
    view.refresh_cards(drive)
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert (drawn["card"]["status"], drawn["card"]["options"], drawn["card"]["waits"]) == ("needs_you", ["Approve", "Hold"], "merge:Forge-AI:monorepo:42")
    release = "the dashboard release carrying [retry flaky uploads](https://github.com/Forge-AI/monorepo/pull/7) is out"
    assert drawn["card"]["prs"][0]["lands"] == f"you approve, CI passes and {release}"
    assert card.render(pr_json(), entry(tap="Approve", checks="pass", now="waiting"), (), (release,))["prs"][0]["blocks"] == f"Approved by you. Lands once {release}"


def test_a_tap_card_whose_body_has_only_a_title_draws_with_approve(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json(body="")))
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert (drawn["card"]["status"], drawn["card"]["options"]) == ("needs_you", ["Approve", "Hold"])
    assert "refused" not in [event["kind"] for event in events(drive)]


def test_a_drive_tooling_pr_whose_his_words_say_none_draws_its_card_with_approve(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## His words, step by step")[0] + "## His words, step by step\nNone: drive tooling.\n"
    monkeypatch.setattr(card, "run", answering(pr_json(body=body)))
    assert cli.main(["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a tooling PR lands on his Approve", "--drive", drive]) == 0
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["serves"] = "o2"
    reply = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})
    assert [(drawn["number"], drawn["card"]["status"]) for drawn in reply["cards"]] == [(42, "needs_you")]
    assert "refused" not in [event["kind"] for event in events(drive)]


@pytest.mark.parametrize(
    ("held", "labels", "pr_state", "why"),
    [
        ({"exempt": "pure port"}, [], "OPEN", 'its exempt class "pure port" lands it with no tap'),
        ({"tap": {"label": "Approve", "at": "2026-11-09T08:00:00-08:00", "head": HEAD, "source": "slack"}}, [], "OPEN", "he approved it already"),
        ({"tap": {"label": "Hold", "at": "2026-11-09T08:00:00-08:00", "head": HEAD, "source": "slack"}}, [], "OPEN", "he held it"),
        ({}, [{"name": github.HOLD_LABEL}], "OPEN", "it carries the hold label"),
        ({}, [], "MERGED", "GitHub reads it merged"),
    ],
)
def test_a_pr_his_card_would_offer_no_approve_draws_no_fresh_card_and_wakes_no_one_yet_its_posted_card_redraws(
    drive: str, monkeypatch: pytest.MonkeyPatch, held: dict, labels: list[dict], pr_state: str, why: str
) -> None:
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"] |= held
    monkeypatch.setattr(card, "run", answering(pr_json(pr_state=pr_state) | {"labels": labels}))
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]}) == {
        "cards": [],
        "skipped": [{"repo": "Forge-AI/monorepo", "number": 42, "reason": f"{why}, so no PR card draws, since it would offer him no Approve"}],
    }
    assert "refused" not in [event["kind"] for event in events(drive)]
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}], "redraw": True})["cards"]
    assert "options" not in drawn["card"]


def test_a_card_that_waits_on_no_tap_draws_without_what_he_will_hate_most(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## What you'll hate most")[0]
    monkeypatch.setattr(card, "run", answering(pr_json(body=body, pr_state="MERGED")))
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}], "redraw": True})["cards"]
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


def test_a_refusal_and_a_tap_are_written_with_the_lane_hearing_their_order_then(drive: str) -> None:
    assert ask({"refused": [{"repo": "Forge-AI/monorepo", "number": 42, "reason": "card needs a title"}]}) == {"refused": 1}
    assert cli.main(["order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page", "--drive", drive]) == 0
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["order"] = "o1"
        cli.bind_subcoordinator(data, "Iris", {"session": "iris-1", "terminal": None, "cwd": "/work", "started": state.stamp()})
    answer = {"repo": "Forge-AI/monorepo", "number": 42, "waits": "", "label": "Approve", "heads": {}, "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert [(event["kind"], event["lane"]) for event in events(drive)] == [("refused", "root"), ("tap", "iris")]


def test_a_tap_goes_to_the_drive_whose_entry_for_the_pr_was_updated_last(drive: str) -> None:
    copy = judged_job(state.new_drive("copy", "coordinator", "s2", "/work", "D123"))
    copy["prs"] = {"Forge-AI/monorepo#42": {**kept_entry(), "updated": "2026-11-11T00:20:00-08:00"}}
    state.create(copy)
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["updated"] = "2026-11-11T00:42:12-08:00"
    assert card.drive_prs()["Forge-AI/monorepo#42"][0] == drive
    with state.edit("copy") as data:
        data["prs"]["Forge-AI/monorepo#42"]["updated"] = "2026-11-11T00:43:00-08:00"
    assert card.drive_prs()["Forge-AI/monorepo#42"][0] == "copy"


def test_an_answer_on_a_drives_question_card_is_posted_as_its_one_answer_record_and_kept_for_history_by_its_question(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[list[str], list[dict]]] = []
    url = "https://acme.slack.com/archives/D123/p1794200000000100"
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"questions": [{"question": "Ring or knock?", "url": url, "card": {"waits": f"{drive}:j3-661440"}}]})
    view.refresh_cards(drive)
    ok = subprocess.CompletedProcess([], 0, "661500\n", "")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append((argv, json.loads(kwargs["input"]))) or ok)
    tap = {"waits": f"{drive}:j3-661440", "label": "Ring", "url": url, "action_ts": "1794200001.000100", "source": "slack"}
    assert ask({"answer": tap}) == {"ok": True, "seq": "661500"}
    ask({"answer": tap | {"label": "neither, keep both", "other": True, "action_ts": "1794200002.000100"}})
    ask({"answer": tap | {"undone": True, "action_ts": "1794200003.000100"}})
    ask({"answer": tap | {"waits": f"{drive}:hier-1", "label": "Keep both until Friday", "other": True, "action_ts": "1794200004.000100"}})
    batch = ["cci", "post", "--drive", drive, "--lane", "communicator", "--batch", "-"]
    answer = {"kind": "answer", "to": [f"root@{drive}"], "url": url, "re": ["661440"]}
    assert posted == [
        (batch, [answer | {"text": "Ring", "attr": {"job": "j3"}, "id": f"tap:{drive}:j3-661440:1794200001.000100"}]),
        (batch, [answer | {"text": "neither, keep both", "attr": {"maps": "none", "job": "j3"}, "id": f"tap:{drive}:j3-661440:1794200002.000100"}]),
        (batch, [answer | {"text": "undone: Ring", "attr": {"job": "j3", "undone": "Ring"}, "id": f"tap:{drive}:j3-661440:1794200003.000100:undone"}]),
        (
            batch,
            [
                {
                    "kind": "answer",
                    "to": [f"root@{drive}"],
                    "url": url,
                    "topic": "card:1794200000.000100",
                    "text": "Keep both until Friday",
                    "attr": {"maps": "none"},
                    "id": f"tap:{drive}:hier-1:1794200004.000100",
                }
            ],
        ),
    ]
    assert [(event["kind"], event["label"], event["question"], event["url"]) for event in events(drive)] == [
        ("answered", "Ring", "Ring or knock?", url),
        ("answered", "neither, keep both", "Ring or knock?", url),
        ("undone", "Ring", "Ring or knock?", url),
        ("answered", "Keep both until Friday", "Ring or knock?", url),
    ]
    assert card.respond({"answer": tap | {"waits": "step-3:merge"}}) == {
        "ok": False,
        "why": "step-3:merge is no drive's question card, so his tap on it stays with the session it reached",
    }


OLDER_CARD = "https://acme.slack.com/archives/D123/p1794100000000100"
NEWER_CARD = "https://acme.slack.com/archives/D123/p1794200000000100"


def two_questions_of_j3(drive: str, monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, str, str, bool]], list[list[dict]]]:
    """j3's older question on one card and its newer one on another, as cc-slack lists his cards; each card the drive closes in place, and each record batch posted."""
    with state.edit(drive) as data:
        data["jobs"]["j3"] = state.new_job("o1", "builder", "the door", [], "2026-11-12T08:00:00-08:00")
        data["jobs"]["j3"]["questions"] = [
            state.new_question("asks/j3-bell.md", "Bell or knocker?", "2026-11-12T08:10:00-08:00", card=OLDER_CARD) | {"waits": f"{drive}:j3-661000"},
            state.new_question("asks/j3-ring.md", "Ring or knock?", "2026-11-12T08:20:00-08:00", card=NEWER_CARD) | {"waits": f"{drive}:j3-661440"},
        ]
    shown = [
        {"question": "Bell or knocker?", "url": OLDER_CARD, "card": {"waits": f"{drive}:j3-661000"}, "options": ["Bell", "Knocker"]},
        {"question": "Ring or knock?", "url": NEWER_CARD, "card": {"waits": f"{drive}:j3-661440"}, "options": ["Ring", "Knock"]},
    ]
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"questions": shown})
    view.refresh_cards(drive)
    answered: list[tuple[str, str, str, bool]] = []
    posted: list[list[dict]] = []
    ok = subprocess.CompletedProcess([], 0, "661500\n", "")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append(json.loads(kwargs["input"])) or ok)
    monkeypatch.setattr(superseded, "answer_card", lambda card, words, link, settled: answered.append((card, words, link, settled)) or {})
    return answered, posted


def closes(question: dict) -> tuple[str | None, ...] | None:
    closed = question["closed"]
    return closed and (closed["how"], closed["words"], closed["link"], closed["seq"], closed["by"])


def test_his_tap_on_a_jobs_newer_question_ends_the_older_one_it_decides_whose_card_follows_and_its_undo_reopens_only_the_tapped_one(
    drive: str, monkeypatch: pytest.MonkeyPatch, jev: Jev
) -> None:
    answered, _ = two_questions_of_j3(drive, monkeypatch)
    jev.changes = {"q0": 0.95}
    tap = {"waits": f"{drive}:j3-661440", "label": "Ring", "url": NEWER_CARD, "action_ts": "1794200001.000100", "source": "slack"}
    assert ask({"answer": tap}) == {"ok": True, "seq": "661500"}
    job = state.load(drive)["jobs"]["j3"]
    assert [closes(question) for question in job["questions"]] == [
        ("superseded", "Ring", NEWER_CARD, "661500", f"{drive}:j3-661440"),
        ("answered", "Ring", NEWER_CARD, "661500", None),
    ]
    assert answered == [(OLDER_CARD, "Ring", NEWER_CARD, False)]
    assert [answer["seq"] for answer in job["undelivered"]] == ["661500"]
    ask({"answer": tap | {"undone": True, "action_ts": "1794200002.000100"}})
    job = state.load(drive)["jobs"]["j3"]
    assert [closes(question) for question in job["questions"]] == [("superseded", "Ring", NEWER_CARD, "661500", f"{drive}:j3-661440"), None]
    assert (job["undelivered"], [event["kind"] for event in events(drive)]) == ([], ["answered", "undone"])


def test_a_tap_cc_slack_runs_again_after_its_card_command_was_cut_short_posts_its_one_record_keeps_his_answer_once_and_finishes_what_was_left(
    drive: str, monkeypatch: pytest.MonkeyPatch, jev: Jev
) -> None:
    answered, posted = two_questions_of_j3(drive, monkeypatch)
    tap = {"waits": f"{drive}:j3-661440", "label": "Ring", "url": NEWER_CARD, "action_ts": "1794200001.000100", "source": "slack"}
    jev.answer = None
    assert ask({"answer": tap}) == {"ok": True, "seq": "661500"}
    assert closes(state.load(drive)["jobs"]["j3"]["questions"][0]) is None
    jev.answer, jev.changes = {"type": "label", "choice": "none", "probabilities": {"none": 0.9}, "confidence": 0.9}, {"q0": 0.95}
    assert ask({"answer": tap}) == {"ok": True, "seq": "661500"}
    assert [record["id"] for [record] in posted] == [f"tap:{drive}:j3-661440:1794200001.000100"] * 2
    job = state.load(drive)["jobs"]["j3"]
    assert [(closes(question) or ("open",))[0] for question in job["questions"]] == ["superseded", "answered"]
    assert ([answer["seq"] for answer in job["undelivered"]], [event["kind"] for event in events(drive)]) == (["661500"], ["answered"])
    assert answered == [(OLDER_CARD, "Ring", NEWER_CARD, False)]


def test_answer_stores_his_tap_and_appends_a_tap_event(drive: str) -> None:
    answer = {
        "repo": "Forge-AI/monorepo",
        "number": 42,
        "waits": "merge:Forge-AI:monorepo:42",
        "label": "Approve",
        "heads": {},
        "action_ts": "1762700000.000100",
        "source": "slack",
    }
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["fundamental"] = "rewritten to do the opposite"
    assert ask({"answer": answer}) == {"ok": True}
    stored = state.load(drive)["prs"]["Forge-AI/monorepo#42"]
    tap = stored["tap"]
    assert (tap["label"], tap["head"], tap["source"], stored["fundamental"]) == ("Approve", HEAD, "slack", None)
    [event] = events(drive)
    assert {key: event[key] for key in ("kind", "repo", "number", "label")} == {
        "kind": "tap",
        "repo": "Forge-AI/monorepo",
        "number": 42,
        "label": "Approve",
    }


def test_a_tap_cc_slack_applies_again_keeps_its_first_time_and_appends_no_second_event(drive: str) -> None:
    answer = {"repo": "Forge-AI/monorepo", "number": 42, "waits": "merge:Forge-AI:monorepo:42", "label": "Approve", "heads": {}, "action_ts": "1762700000.000100", "source": "slack"}
    assert ask({"answer": answer}) == {"ok": True}
    first = state.load(drive)["prs"]["Forge-AI/monorepo#42"]["tap"]
    assert ask({"answer": answer}) == {"ok": True}
    assert (state.load(drive)["prs"]["Forge-AI/monorepo#42"]["tap"], len(events(drive))) == (first, 1)
    assert ask({"answer": answer | {"label": "Hold", "action_ts": "1762700100.000100"}}) == {"ok": True}
    assert (state.load(drive)["prs"]["Forge-AI/monorepo#42"]["tap"]["label"], len(events(drive))) == ("Hold", 2)


def test_his_approve_starts_the_lander_on_a_green_monorepo_pr_at_once_and_leaves_a_red_one_to_the_watch(
    drive: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    assert cli.main(["order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page", "--drive", drive]) == 0
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    view.refresh_cards(drive)
    with state.edit(drive) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "the run page", [], state.stamp())
        data["prs"]["Forge-AI/monorepo#42"] |= {"order": "o1", "checks": "fail", "review": "approved"}
    answer = {"repo": "Forge-AI/monorepo", "number": 42, "waits": "merge:Forge-AI:monorepo:42", "label": "Approve", "heads": {}, "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert landers == []
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["checks"] = "pass"
    assert ask({"answer": answer}) == {"ok": True}
    assert landers == [("Forge-AI/monorepo#42",)]


def moved_under_his_card(drive: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """His card drawn at HEAD, then a push of a new change to MOVED, which the watch read and no check judged yet; the card request that redraws it."""
    asked = {"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]}
    assert cli.main(["order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page", "--drive", drive]) == 0
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    view.refresh_cards(drive)
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"] |= {"checks": "pass", "review": "approved"}
    monkeypatch.setattr(card, "run", answering(pr_json()))
    assert ask(asked)["cards"][0]["card"]["options"] == ["Approve", "Hold"]
    monkeypatch.setattr(card, "run", answering(pr_json() | {"headRefOid": MOVED}))
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"] |= {"head": MOVED, "checked": None}
    return asked | {"redraw": True}


def tap_approve(shown: str) -> None:
    answer = {"repo": "Forge-AI/monorepo", "number": 42, "waits": "merge:Forge-AI:monorepo:42", "label": "Approve", "heads": {"Forge-AI/monorepo#42": shown}, "action_ts": "", "source": "pressed the button"}
    assert ask({"answer": answer}) == {"ok": True}


def test_a_head_that_moved_under_his_card_keeps_approve_and_his_approve_of_the_head_he_saw_lands_it_with_no_check_at_the_new_head(
    drive: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    key = "Forge-AI/monorepo#42"
    redraw = moved_under_his_card(drive, monkeypatch)
    [shown] = ask(redraw)["cards"]
    assert (shown["card"]["status"], shown["card"]["options"], shown["card"]["prs"][0]["lands"], shown["card"]["prs"][0]["blocks"]) == ("needs_you", ["Approve", "Hold"], "you approve", "")
    tap_approve(HEAD)
    [tapped] = [event for event in events(drive) if event["kind"] == "tap"]
    assert (state.load(drive)["prs"][key]["tap"]["head"], tapped["head"], landers) == (HEAD, HEAD, [(key,)])
    [held] = ask(redraw)["cards"]
    assert ("options" in held["card"], held["card"]["prs"][0]["blocks"]) == (False, "Approved by you. CI passed; the drive queues it for merge")


def test_a_change_against_his_words_is_a_whole_line_on_his_card_beside_approve_and_his_approve_still_lands_it(
    drive: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], redraws: Redraws, judge: Judge
) -> None:
    key, change = "Forge-AI/monorepo#42", "run.ts drops the run page " + "and its link from the sidebar " * 20
    named = f'o1 "ship the run page": {change}'
    redraw = moved_under_his_card(drive, monkeypatch)
    judge.against = [{"order": "o1", "words": "ship the run page", "file": "run.ts", "change": change}]
    assert cli.main(["pr", "check", URL, "--drive", drive]) == 0
    [checked] = [event for event in events(drive) if event["kind"] == "check"]
    assert checked["change"].startswith(f"NOT READY: {key} go against his words, judged from each PR's title and diff: {named}. ")
    line = f"Commit fedcba9 goes against your words: {named}. Its agent is told; your Approve still lands it"
    [asking] = ask(redraw)["cards"]
    assert (asking["card"]["options"], asking["card"]["prs"][0]["review"], redraws.asked) == (["Approve", "Hold"], line, [URL])
    tap_approve(MOVED)
    pr = state.load(drive)["prs"][key]
    assert (pr["tap"]["label"], pr["fundamental"], landers) == ("Approve", None, [(key,)])
    [held] = ask(redraw)["cards"]
    approved = f"Commit fedcba9 goes against your words: {named}. Your Approve still lands it"
    assert (held["card"]["prs"][0]["blocks"], held["card"]["prs"][0]["review"]) == ("Approved by you. CI passed; the drive queues it for merge", approved)


def test_answer_matches_the_lowercase_repo_cc_slack_sends(drive: str) -> None:
    answer = {"repo": "forge-ai/monorepo", "number": 42, "waits": "", "label": "Hold", "heads": {}, "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert state.load(drive)["prs"]["Forge-AI/monorepo#42"]["tap"]["label"] == "Hold"


def test_answer_on_a_merged_pr_stores_nothing(drive: str) -> None:
    answer = {"repo": "Forge-AI/monorepo", "number": 7, "waits": "", "label": "Approve", "heads": {}, "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert state.load(drive)["prs"]["Forge-AI/monorepo#7"]["tap"] is None
    assert events(drive) == []


def test_answer_on_an_unknown_pr_fails(drive: str) -> None:
    answer = {"repo": "o/n", "number": 1, "waits": "", "label": "Approve", "heads": {}, "action_ts": "", "source": "s"}
    with pytest.raises(state.DriveError, match="not a drive PR"):
        ask({"answer": answer})


@pytest.mark.parametrize(
    "tap",
    [
        {"answer": {"repo": "Forge-AI/monorepo", "number": 42, "waits": "merge:Forge-AI:monorepo:42", "label": "Approve", "heads": {}, "action_ts": "", "source": "slack"}},
        {"answer": {"waits": "demo:j3-661440", "label": "Ring", "url": "https://acme.slack.com/archives/D123/p1794200000000100", "action_ts": "", "source": "slack"}},
    ],
)
def test_his_tap_on_any_card_records_him_heard_and_brings_the_catch_up_he_was_held_for_reading_nothing_from_github(
    drive: str, monkeypatch: pytest.MonkeyPatch, tap: dict
) -> None:
    heard, tapped = "2026-11-11T12:36:02-08:00", state.parse_time("2026-11-11T13:57:22-08:00")
    with state.edit(drive) as data:
        data["owner"] = {"heard": heard, "away": False}
        data["held"]["665753"] = {"order": None, "at": "2026-11-11T13:40:00-08:00", "told": "665754"}
    monkeypatch.setattr(state, "now", lambda: tapped)
    monkeypatch.setattr(card, "run", lambda *args, **kwargs: pytest.fail("a tap read GitHub"))
    monkeypatch.setattr(github, "run", lambda *args, **kwargs: pytest.fail("a tap read GitHub"))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "661500\n", ""))
    ask(tap)
    assert state.load(drive)["owner"] == {"heard": state.stamp(tapped), "away": False}
    assert state.load(drive)["catch_up"] == state.new_catch_up(heard, [])


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
    data = judged_job(state.new_drive("demo", "coordinator", "s1", "/work", "D123"))
    data["prs"] = {f"Forge-AI/monorepo#{number}": {**kept_entry(), "url": stack_url(number)} for number in (41, 42, 43, 44)}
    state.create(data)
    asked: list[list[str]] = []

    def gh(cmd: list[str], **_: str) -> str:
        asked.append(cmd)
        if cmd[1] == "api":
            return "[]"
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


def test_a_stack_cards_pictures_titled_alike_are_numbered_apart_so_each_pr_keeps_its_own(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    stacked_gh = card.run

    def gh(cmd: list[str], **_: str) -> str:
        if cmd[1] == "api":
            return stacked_gh(cmd)
        pr = json.loads(stacked_gh(cmd))
        pr["body"] += f"\n## Show me\n```mermaid\nflowchart TD\n  a --> b{cmd[3]}\n```\n"
        return json.dumps(pr)

    monkeypatch.setattr(card, "run", gh)
    [drawn] = ask(asks(41, 42))["cards"]
    assert [[Path(path).name for path in row["pictures"]] for row in drawn["card"]["prs"]] == [["Diagram"], ["Diagram (2)"]]


def test_a_stack_whose_show_me_diagram_does_not_draw_is_refused_with_mermaids_words_instead_of_carded_without_it(
    stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch, chrome: Chrome
) -> None:
    stacked_gh = card.run

    def gh(cmd: list[str], **_: str) -> str:
        if cmd[1] == "api":
            return stacked_gh(cmd)
        pr = json.loads(stacked_gh(cmd))
        pr["body"] += "\n## Show me\n```mermaid\nsequenceDiagram\n  Note over D: draws each picture; refuses one that fails\n```\n"
        return json.dumps(pr)

    monkeypatch.setattr(card, "run", gh)
    chrome.refusal = "Parse error on line 2: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'"
    drawn = ask(asks(41, 42))
    assert drawn["cards"] == []
    reason = "monorepo PR 41: its Show me mermaid does not draw: Parse error on line 2: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'"
    assert [skipped["reason"] for skipped in drawn["skipped"]] == [reason, reason]
    assert [state.load("demo")["prs"][f"Forge-AI/monorepo#{number}"]["refused"] for number in (41, 42)] == [reason, reason]
    chrome.refusal = None
    [carded] = ask(asks(41, 42))["cards"]
    assert [[Path(path).name for path in row["pictures"]] for row in carded["card"]["prs"]] == [["Diagram"], ["Diagram (2)"]]


def test_a_native_stack_of_six_prs_draws_one_card_whose_approve_taps_each(stacked: list[list[str]], native_stacks: dict[str, list[tuple[str, str]]]) -> None:
    numbers = (41, 42, 43, 44, 45, 46)
    keys = [f"Forge-AI/monorepo#{n}" for n in numbers]
    with state.edit("demo") as data:
        for key in keys[4:]:
            data["prs"][key] = {**kept_entry(), "url": state.pr_url(key)}
    for key in keys:
        native_stacks[key] = [(member, "open") for member in keys]
    [drawn] = ask(asks(*numbers))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(n) for n in numbers]
    assert (drawn["card"]["waits"], drawn["card"]["options"]) == ("merge:Forge-AI:monorepo:41-46", ["Approve", "Hold"])
    ask({"answer": {"repo": "Forge-AI/monorepo", "number": 41, "waits": drawn["card"]["waits"], "label": "Approve", "heads": {}, "action_ts": "", "source": "slack"}})
    assert [state.load("demo")["prs"][key]["tap"]["label"] for key in keys] == ["Approve"] * 6


def test_a_stack_with_one_pr_reworked_to_his_words_draws_no_card_a_redraw_included_until_its_builders_ready_meets_them(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    with state.edit("demo") as data:
        state.rework(data["prs"]["Forge-AI/monorepo#42"], "o2", "Topology goes in the meta table")
    reason = 'Forge-AI/monorepo#42 is being reworked to his words "Topology goes in the meta table", so no card asks his tap until its builder\'s READY says how they are met'
    for drawn in (ask(asks(41, 42, 43)), ask(asks(42) | {"redraw": True})):
        assert (drawn["cards"], [skipped["reason"] for skipped in drawn["skipped"]]) == ([], [reason] * 3)
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#42"].update(rework=None, fundamental='Reworked to your words "Topology goes in the meta table": it reads the meta table')
    [drawn] = ask(asks(41, 42, 43))["cards"]
    assert drawn["card"]["prs"][1]["changed"] == 'Reworked to your words "Topology goes in the meta table": it reads the meta table'


def test_a_stack_whose_every_pr_asking_his_tap_waits_on_his_open_card_draws_one_card_saying_each_lands_once_he_answers(stacked: list[list[str]]) -> None:
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#41"]["waits"] = ["demo:j1-7-a"]
        data["prs"]["Forge-AI/monorepo#42"]["waits"] = ["demo:j1-7-a"]
        data["prs"]["Forge-AI/monorepo#43"]["wants_tap"] = False
    [drawn] = ask(asks(41, 42, 43))["cards"]
    assert all("you answer" in row["lands"] for row in drawn["card"]["prs"][:2])


def test_a_redraw_of_one_pr_in_a_stack_redraws_the_whole_stack(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    [drawn] = ask(asks(42))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(41), stack_url(42), stack_url(43)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:41-43"


def test_a_stack_of_three_merged_prs_on_one_order_redraws_as_done(
    stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = [f"Forge-AI/monorepo#{n}" for n in (41, 42, 43)]
    ask(asks(41, 42, 43))
    with state.edit("demo") as data:
        for key in keys:
            data["prs"][key]["state"] = "merged"
    stacked_gh = card.run
    monkeypatch.setattr(card, "run", lambda cmd, **_: stacked_gh(cmd) if cmd[1] == "api" else json.dumps(json.loads(stacked_gh(cmd)) | {"state": "MERGED"}))
    [drawn] = ask(asks(41, 42, 43) | {"redraw": True})["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(41), stack_url(42), stack_url(43)]
    assert drawn["card"]["status"] == "done"


def test_a_pr_added_to_the_stack_later_gets_its_own_card(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    [drawn] = ask(asks(44))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(44)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:44"
    assert state.load("demo")["prs"]["Forge-AI/monorepo#44"]["stack"] == ["Forge-AI/monorepo#44"]


def test_a_pr_added_on_top_joins_the_stack_card_when_asked_with_it(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    [drawn] = ask(asks(41, 42, 43, 44))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(n) for n in (41, 42, 43, 44)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:41-44"
    keys = [f"Forge-AI/monorepo#{n}" for n in (41, 42, 43, 44)]
    assert all(state.load("demo")["prs"][key]["stack"] == keys for key in keys)
    [again] = ask(asks(43))["cards"]
    assert again["card"]["waits"] == "merge:Forge-AI:monorepo:41-44"


def test_a_stack_split_by_an_ask_missing_a_member_draws_one_card_once_asked_whole(stacked: list[list[str]]) -> None:
    ask(asks(41, 43, 44))
    assert state.load("demo")["prs"]["Forge-AI/monorepo#43"]["stack"] == ["Forge-AI/monorepo#43", "Forge-AI/monorepo#44"]
    [drawn] = ask(asks(41, 42, 43, 44))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(n) for n in (41, 42, 43, 44)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:41-44"
    assert not any(state.load("demo")["prs"][f"Forge-AI/monorepo#{n}"].get("refused") for n in (41, 42, 43, 44))


def siblings(monkeypatch: pytest.MonkeyPatch, orders: dict[int, str], drawn_alone: bool = False) -> list[str]:
    keys = [f"Forge-AI/monorepo#{number}" for number in orders]
    with state.edit("demo") as data:
        for number, order in orders.items():
            key = f"Forge-AI/monorepo#{number}"
            served = {"serves": order, "checked": {"head": HEAD, "read": HEAD, "orders": ["o1", order], "lines": {}}}
            data["prs"][key] = {**kept_entry(), "url": stack_url(number), **served, **({"stack": [key]} if drawn_alone else {})}
    monkeypatch.setattr(
        card,
        "run",
        lambda cmd, **_: "[]" if cmd[1] == "api" else json.dumps(pr_json(title="Fix: one move of the cutover", url=stack_url(int(cmd[3])), base="main", branch=f"cutover-{cmd[3]}")),
    )
    return keys


@pytest.mark.parametrize("drawn_alone", [False, True])
def test_prs_of_one_order_in_no_native_stack_asked_together_draw_one_card_whose_approve_covers_each(
    stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch, drawn_alone: bool
) -> None:
    keys = siblings(monkeypatch, {54591: "o601", 54602: "o601", 54608: "o601"}, drawn_alone)
    [drawn] = ask(asks(54591, 54602, 54608))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(n) for n in (54591, 54602, 54608)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:54591-54608"
    assert all(state.load("demo")["prs"][key]["stack"] == keys for key in keys)
    ask({"answer": {"repo": "Forge-AI/monorepo", "number": 54591, "waits": drawn["card"]["waits"], "label": "Approve", "heads": {}, "action_ts": "", "source": "slack"}})
    assert [state.load("demo")["prs"][key]["tap"]["label"] for key in keys] == ["Approve"] * 3


def test_prs_in_no_native_stack_draw_one_card_per_order_they_serve(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    siblings(monkeypatch, {51: "o601", 52: "o602", 53: "o601"})
    drawn = ask(asks(51, 52, 53))["cards"]
    assert [[row["url"] for row in each["card"]["prs"]] for each in drawn] == [[stack_url(51), stack_url(53)], [stack_url(52)]]


def test_five_prs_of_one_order_in_no_native_stack_draw_one_card(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    siblings(monkeypatch, dict.fromkeys((51, 52, 53, 54, 55), "o601"))
    drawn = ask(asks(51, 52, 53, 54, 55))
    assert [[row["url"] for row in each["card"]["prs"]] for each in drawn["cards"]] == [[stack_url(n) for n in (51, 52, 53, 54, 55)]]
    assert drawn["skipped"] == []


def test_a_base_branch_chain_keeps_its_own_card_beside_a_sibling_pr_of_its_order_as_the_watch_names_them(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#50"] = {**kept_entry(), "url": stack_url(50)}
    stacked_gh = card.run
    sibling = json.dumps(pr_json(title="Fix: a sibling on main", url=stack_url(50), base="main", branch="sibling-50"))
    monkeypatch.setattr(card, "run", lambda cmd, **_: sibling if cmd[1] != "api" and cmd[3] == "50" else stacked_gh(cmd))
    drawn = ask(asks(41, 42, 50))["cards"]
    assert [[row["url"] for row in each["card"]["prs"]] for each in drawn] == [[stack_url(41), stack_url(42)], [stack_url(50)]]


def test_a_chain_that_cuts_through_a_stored_stack_keeps_every_pr_on_one_card(stacked: list[list[str]]) -> None:
    ask(asks(41, 42))
    with state.edit("demo") as data:
        for n in (43, 44):
            data["prs"][f"Forge-AI/monorepo#{n}"]["stack"] = ["Forge-AI/monorepo#43", "Forge-AI/monorepo#44"]
    drawn = ask(asks(41, 42, 43))["cards"]
    rows = [row["url"] for each in drawn for row in each["card"]["prs"]]
    assert sorted(rows) == sorted(set(rows))
    assert state.load("demo")["prs"]["Forge-AI/monorepo#41"]["stack"] == ["Forge-AI/monorepo#41", "Forge-AI/monorepo#42"]


def test_a_grown_stack_refused_keeps_the_stack_it_had(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    with state.edit("demo") as data:
        state.rework(data["prs"]["Forge-AI/monorepo#44"], "o2", "Topology goes in the meta table")
    assert ask(asks(41, 42, 43, 44))["cards"] == []
    keys = [f"Forge-AI/monorepo#{n}" for n in (41, 42, 43)]
    assert state.load("demo")["prs"]["Forge-AI/monorepo#41"]["stack"] == keys
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
        "heads": {},
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
    answer = {"repo": "Forge-AI/monorepo", "number": 41, "waits": "merge:Forge-AI:monorepo:41", "label": "Hold", "heads": {}, "action_ts": "", "source": "s"}
    ask({"answer": answer})
    assert [event["number"] for event in events("demo")] == [41]


def typed(number: int, label: str) -> None:
    assert cli.main(["pr", "set", stack_url(number), "--tapped", label, "--tap-source", HIS_TAPS, "--drive", "demo"]) == 0


def on_his_stack_card(monkeypatch: pytest.MonkeyPatch, *numbers: int) -> str:
    """His stack card drawn on the PRs, cc-slack listing it open as his card for each; its waits id."""
    assert cli.main(["order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page", "--drive", "demo"]) == 0
    [drawn] = ask(asks(*numbers))["cards"]
    posted = {"url": "https://acme.slack.com/archives/D123/p1794515083771169", "prs": [stack_url(n) for n in numbers], "asked": "2026-11-12T12:24:41-08:00", "card": drawn["card"]}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [posted], "closed": []})
    return drawn["card"]["waits"]


def test_his_typed_approve_of_one_pr_and_hold_of_the_other_redraw_their_stack_card_in_place_with_no_buttons_saying_each_and_a_later_tap_redraws_it_until_it_is_replaced(
    stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch, redraws: Redraws, capsys: pytest.CaptureFixture[str]
) -> None:
    on_his_stack_card(monkeypatch, 41, 42)
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#41"]["checks"] = "fail"
    capsys.readouterr()
    typed(41, "Approve")
    assert capsys.readouterr().out.strip().endswith("redrawing in place the 1 open card(s) of his carrying it, to show his Approve")
    typed(42, "Hold")
    assert redraws.asked == [stack_url(41), stack_url(42)]
    [redrawn] = ask(asks(41, 42) | {"redraw": True})["cards"]
    assert (redrawn["card"]["status"], "options" in redrawn["card"], "waits" in redrawn["card"]) == ("info", False, False)
    assert [row["blocks"] for row in redrawn["card"]["prs"]] == ["Approved by you. CI failed; the agent is fixing it", "Held by you"]
    collapsed = {"url": "https://acme.slack.com/archives/D123/p1794515083771169", "prs": [stack_url(41), stack_url(42)], "waits": "merge:Forge-AI:monorepo:41-42", "answer": None, "replaced_by": "", "tapped": "This PR no longer waits on your tap."}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": [collapsed]})
    typed(42, "Approve")
    assert redraws.asked == [stack_url(41), stack_url(42), stack_url(42)]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": [collapsed | {"replaced_by": "https://acme.slack.com/archives/D123/p1794515999000000"}]})
    typed(42, "Hold")
    assert redraws.asked == [stack_url(41), stack_url(42), stack_url(42)]


def test_a_tap_on_his_stack_card_after_his_typed_approve_of_one_pr_keeps_that_approve_and_taps_the_rest(
    stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    waits = on_his_stack_card(monkeypatch, 41, 42)
    typed(41, "Approve")
    [asking] = ask(asks(41, 42) | {"redraw": True})["cards"]
    assert (asking["card"]["options"], [row["blocks"] for row in asking["card"]["prs"]]) == (["Approve", "Hold"], ["Approved by you. Lands once CI passes", ""])
    ask({"answer": {"repo": "Forge-AI/monorepo", "number": 41, "waits": waits, "label": "Hold", "heads": {}, "action_ts": "1794516000.000001", "source": "slack"}})
    prs = state.load("demo")["prs"]
    assert [prs[f"Forge-AI/monorepo#{n}"]["tap"]["label"] for n in (41, 42)] == ["Approve", "Hold"]
    assert [(event["kind"], event["number"], event["label"]) for event in events("demo") if event["kind"] == "tap"] == [("tap", 42, "Hold")]


def test_a_pr_in_a_native_stack_draws_its_native_stack_and_the_rest_of_the_chain_it_was_drawn_on_chain_by_base(
    stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch, native_stacks: dict[str, list[tuple[str, str]]]
) -> None:
    keys = {n: f"Forge-AI/monorepo#{n}" for n in (41, 42, 43, 44, 45, 46)}
    with state.edit("demo") as data:
        for n in (45, 46):
            data["prs"][keys[n]] = {**kept_entry(), "url": stack_url(n)}
        data["prs"][keys[43]]["state"] = "closed"
        for key in keys.values():
            data["prs"][key]["stack"] = list(keys.values())
    for n in (41, 42, 43, 44):
        native_stacks[keys[n]] = [("Forge-AI/monorepo#40", "open"), (keys[41], "open"), (keys[42], "open"), (keys[43], "closed"), (keys[44], "open")]
    loose = {45: ("Fix: retry the upload", "main", "retry-1"), 46: ("Fix: retry the download", "retry-1", "retry-2")}
    stacked_gh = card.run

    def gh(cmd: list[str], **_: str) -> str:
        if cmd[1] == "api" or int(cmd[3]) not in loose:
            return stacked_gh(cmd)
        title, base, branch = loose[int(cmd[3])]
        return json.dumps(pr_json(title=title, url=stack_url(int(cmd[3])), base=base, branch=branch))

    monkeypatch.setattr(card, "run", gh)
    drawn = ask(asks(41, 42, 44, 45, 46))["cards"]
    assert [[row["url"] for row in each["card"]["prs"]] for each in drawn] == [[stack_url(n) for n in (41, 42, 44)], [stack_url(45), stack_url(46)]]
    assert [each["card"]["waits"] for each in drawn] == ["merge:Forge-AI:monorepo:41-44", "merge:Forge-AI:monorepo:45-46"]
    assert state.load("demo")["prs"][keys[44]]["stack"] == [keys[41], keys[42], keys[44]]


def test_a_pr_added_to_its_native_stack_after_the_stack_card_was_drawn_gets_its_own_card(
    stacked: list[list[str]], native_stacks: dict[str, list[tuple[str, str]]]
) -> None:
    keys = [f"Forge-AI/monorepo#{n}" for n in (41, 42, 43, 44)]
    for key in keys[:3]:
        native_stacks[key] = [(member, "open") for member in keys[:3]]
    ask(asks(41, 42, 43))
    for key in keys:
        native_stacks[key] = [(member, "open") for member in keys]
    [drawn] = ask(asks(44))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(44)]
    [again] = ask(asks(42) | {"redraw": True})["cards"]
    assert again["card"]["waits"] == "merge:Forge-AI:monorepo:41-43"
