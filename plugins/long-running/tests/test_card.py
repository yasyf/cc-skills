import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from drive import card, cli, limits, picture, state, view
from drive.github import GithubError

URL = "https://github.com/Forge-AI/monorepo/pull/42"
HEAD = "0123456789abcdef0123456789abcdef01234567"
RUNNING = [{"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS", "conclusion": ""}]
PASSING = [
    {"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"__typename": "StatusContext", "context": "buildkite", "state": "SUCCESS"},
]
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
RENEW_EXCHANGE = """The same agent at minute 10 and minute 15, before and after:

```sh
before  00:15  SandSQL inbox read with the pushed pass        -> refused, the pass expired
before  00:15  model call through the api as "Agent <token>"  -> 401, the token expired
after   00:10  POST /agent-hosts/agt_.../permission/renew {"writer":1}
               -> 200 {"token":"eyJ...","tokenExpiresAt":"...T00:25:00Z","inbox":{"db":"inbox-agt_...","token":"sat1.inbox",...}}
after   00:10  the same renew against an api replica from before this change -> 404, and the agent keeps its hold
after   00:15  inbox read and model call on the renewed pair  -> 200
after   00:10  the same ack from a pod that no longer holds the agent -> 409 {"error":"over"}, no pass minted
```
"""
SESSIONS_EXCHANGE = """Each page of chats the list asks for.

```sh
# launch: the newest 50, plus where the next page starts
GET /api/sessions?team=team_acme
-> { "sessions": [50 chats], "next": "1794294151630439:sess_01k..." }

# a Show more click: every chat down to that cursor, then 50 older
GET /api/sessions?team=team_acme&through=1794294151630439%3Asess_01k...
-> { "sessions": [100 chats], "next": "1794201022118000:sess_01h..." }
```
"""
CONFLICT_RESPONSE = """What a pod that lost its hold gets back.

```http
HTTP/1.1 409 Conflict
content-type: application/json

{"error": "over"}
```
"""
RENEW_REWRITTEN = """The same agent's calls at minute 15, before and after the pod renews at minute 10.

| call at minute 15 | before | after |
|---|---|---|
| SandSQL inbox read | refused, the pass expired | 200 |
| model call as the agent | 401, the token expired | 200 |
| renew from a pod that lost the agent | no such route | 409, no pass minted |

```mermaid
sequenceDiagram
  participant pod as cloud pod
  participant api
  pod->>api: minute 10, POST /agent-hosts/:hostId/permission/renew
  api-->>pod: a fresh token and inbox pass, good to minute 25
  pod->>api: minute 15, inbox read and model call
  api-->>pod: both answered
```
"""
RENEW_CALL = """The call the pod makes at minute 10.

```http
POST /agent-hosts/agt_1/permission/renew HTTP/1.1
Authorization: Executor <pod token>

{"writer": 1}
```
"""
RENEW_HANDLER = """The handler that answers it.

```go
// POST /agent-hosts/{id}/permission/renew -> 200
func (h *Hosts) Renew(w http.ResponseWriter, r *http.Request) {
	held, err := h.placements.Held(r.Context(), r.PathValue("id"), writer(r))
	if err != nil {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	writeJSON(w, h.mint(held))
}
```
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
        "labels": [],
        "url": url,
    }


def answering(pr: dict) -> Callable[[list[str]], str]:
    """A stand-in for gh answering card.read: the PR without its comments for gh pr view, and its comments for the REST read."""
    return lambda cmd: json.dumps(pr["comments"] if cmd[1] == "api" else {field: value for field, value in pr.items() if field != "comments"})


def entry(wants_tap: bool = True, tap: str | None = None, exempt: str | None = None, pr_state: str = "open") -> dict:
    return {
        "url": URL,
        "job": "j1",
        "order": "o1",
        "wants_tap": wants_tap,
        "tap": {"label": tap, "at": "2026-11-09T08:00:00-08:00", "head": HEAD, "source": "slack"} if tap else None,
        "exempt": exempt,
        "fundamental": None,
        "not_ready": None,
        "state": pr_state,
        "title": "Fix: retry flaky uploads",
        "head": HEAD,
        "checks": "pending",
        "failed_checks": [],
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
        "changed": "",
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
    drawn = card.render(pr_json(rollup=PASSING), changed)
    assert (drawn["status"], drawn["waits"], drawn["prs"][0]["changed"]) == (
        "needs_you",
        "merge:Forge-AI:monorepo:42",
        "rewritten to do the opposite of the version he held",
    )


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


def test_a_check_that_failed_on_this_head_and_is_missing_mid_rerun_does_not_read_as_passed() -> None:
    rerun = {**entry(tap="Approve"), "failed_checks": ["LTX Behavioral Gate (short)"]}
    assert card.render(pr_json(rollup=PASSING), rerun)["prs"][0]["blocks"] == "Lands once CI passes"
    assert card.render(pr_json(rollup=PASSING), {**rerun, "head": "f" * 40})["prs"][0]["blocks"] == "CI passed; the agent is landing it"


def test_a_held_pr_keeps_its_buttons_and_says_held() -> None:
    assert card.render(pr_json(rollup=PASSING), entry(tap="Hold")) == {
        "status": "needs_you",
        "title": "Fix: approve retry flaky uploads?",
        "prs": [row(blocks="Held by you", lands="you approve")],
        "options": ["Approve", "Hold"],
        "recommended": "Approve",
        "waits": "merge:Forge-AI:monorepo:42",
    }


def test_a_pr_the_drive_holds_with_the_hold_label_asks_him_nothing() -> None:
    labeled = pr_json(rollup=PASSING) | {"labels": [{"id": "LA_1", "name": "hold", "description": "", "color": "d93f0b"}]}
    assert card.render(labeled, entry()) == {
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
    ("chart", "refusal"),
    [
        (CHART.replace(ROWS, "[1]"), "the Show me ```chart fence is no chart Slack draws"),
        (CHART.replace('"encoding": {', '"encoding": {"color": "step", '), "the Show me ```chart fence is no chart Slack draws"),
        (CHART.replace('"seconds": 40', '"seconds": "40"'), 'the Show me ```chart fence has a row whose "seconds" is "40", and each value is a number'),
        (CHART.replace('"after"', '"before"'), 'the Show me ```chart fence has the series "seconds" without exactly one row for each category'),
    ],
)
def test_a_pr_show_me_chart_whose_rows_slack_cannot_draw_leaves_the_card_and_is_refused(chart: str, refusal: str) -> None:
    body = f"## Show me\nEach wake, before and after.\n\n```chart\n{chart}\n```\n"
    assert card.render(pr_json(body=body), entry())["prs"][0]["show_me"] == "Each wake, before and after."
    assert refusal in "; ".join(card.show_me_problems(pr_json(body=body)))


@pytest.mark.parametrize(
    ("shown", "refusal"),
    [
        (
            "| run, seconds |\n|---|\n| r1, 4.2 s |",
            'the Show me table headed "run, seconds" has one column, so it compares nothing across its rows: give each field a column of its own',
        ),
        (
            "| run, seconds |\n|---|\n| r1 \\| 4.2 s |",
            'the Show me table headed "run, seconds" has one column, so it compares nothing across its rows: give each field a column of its own',
        ),
        (
            "| size | before | after |\n|---|---|---|\n| 1 GiB | time | time |\n| 2 GiB | time | time |",
            'the Show me table headed "size | before | after" holds "time" in every cell',
        ),
        (
            "```chart\n" + CHART.replace('"before"', '"before the queue drains"') + "\n```",
            'the Show me ```chart fence has the label "before the queue drains", 23 characters, and each label holds 1 to 20, so Slack cannot draw it as its own chart',
        ),
    ],
)
def test_a_pr_show_me_table_or_chart_slack_cannot_draw_as_its_own_is_refused_naming_the_fix(shown: str, refusal: str) -> None:
    problems = card.show_me_problems(pr_json(body=f"## Show me\nEach wake, before and after.\n{shown}\n"))
    assert refusal in "; ".join(problems)


@pytest.mark.parametrize("show_me", [RENEW_EXCHANGE, SESSIONS_EXCHANGE, CONFLICT_RESPONSE], ids=["calls and answers in an sh fence", "requests and JSON answers between comments", "a response dump"])
def test_a_pr_show_me_of_an_http_exchange_is_refused_naming_what_to_draw_instead(show_me: str) -> None:
    problems = "; ".join(card.show_me_problems(pr_json(body=f"## Show me\n{show_me}")))
    assert "is an HTTP exchange, raw data or tool output, and a picture of it shows him nothing" in problems
    assert "a ```mermaid sequenceDiagram for calls between services, a ```compare or a markdown table for options or before and after" in problems


@pytest.mark.parametrize("show_me", [RENEW_REWRITTEN, RENEW_CALL, RENEW_HANDLER], ids=["a table and a sequence diagram", "the request alone", "code naming its route in a comment"])
def test_a_pr_show_me_drawing_what_an_http_exchange_means_or_the_call_alone_passes(show_me: str) -> None:
    assert card.show_me_problems(pr_json(body=f"## Show me\n{show_me}")) == []


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

    def gh(cmd: list[str]) -> str:
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
    assert reply == {
        "cards": [{"repo": "Forge-AI/monorepo", "number": 42, "card": card.render(pr_json(), entry())}],
        "skipped": [{"repo": "yasyf/cc-skills", "number": 42, "reason": "drive card could not read it from GitHub"}],
    }
    assert asked == [
        ["gh", "pr", "view", "42", "-R", "Forge-AI/monorepo", "--json", card.FIELDS],
        ["gh", "api", "--paginate", "repos/Forge-AI/monorepo/issues/42/comments?per_page=100"],
        ["gh", "pr", "view", "42", "-R", "yasyf/cc-skills", "--json", card.FIELDS],
    ]
    err = capsys.readouterr().err
    assert "yasyf/cc-skills#42" in err
    assert "Forge-AI/monorepo#99 is in no drive" in err


def test_prs_matches_the_lowercase_repo_cc_slack_asks_with(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json()))
    reply = ask({"prs": [{"repo": "forge-ai/monorepo", "number": 42}]})
    assert [(drawn["repo"], drawn["number"]) for drawn in reply["cards"]] == [("Forge-AI/monorepo", 42)]


def test_a_tap_card_without_what_he_will_hate_most_is_skipped_and_refused(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.replace("## What you'll hate most", "## Biggest worry")
    monkeypatch.setattr(card, "run", answering(pr_json(body=body)))
    reason = f"its tap card needs a \"What you'll hate most\" section in the body of {URL}"
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]}) == {
        "cards": [],
        "skipped": [{"repo": "Forge-AI/monorepo", "number": 42, "reason": reason}],
    }
    assert [(event["kind"], event["reason"]) for event in events(drive)] == [("refused", reason)]


SNIPPETS_SHOW_ME = """
## Show me

What `up` runs on the box just before its reboot, before and after:

```sh
sudo launchctl enable system/com.example.screensharing
(sleep 1; sudo shutdown -r now) </dev/null >/dev/null 2>&1 &
```

```sh
sudo launchctl enable system/com.example.screensharing
for try in {1..60}; do [[ "$(sudo launchctl print system/com.example.disk-formatter 2>/dev/null)" == *"state = running"* ]] || break; sleep 5; done
(sleep 1; sudo shutdown -r now) </dev/null >/dev/null 2>&1 &
```

The box's first-boot script loses its disk lines:

```sh
disk=$(diskutil list physical external | head -n1 | cut -d" " -f1)
yes | diskutil repairDisk "$disk"
```

On a fresh box the host had formatted the disk before the reboot, and the login came up with no dialog.

```mermaid
flowchart LR
  boot[first boot: scratch disk blank] --> job[the host's job formats it]
  boot --> reboot[up reboots the box]
  job -->|now: up waits for the job| clean[login finds the disk: clean desktop]
```
"""


def test_a_tap_card_whose_show_me_draws_snippets_that_add_nothing_is_refused_naming_the_rule(
    drive: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr(card, "run", answering(pr_json(body=FIX_BODY + SNIPPETS_SHOW_ME)))
    [skipped] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["skipped"]
    assert skipped["reason"].startswith(
        "its tap card needs a Show me whose every picture shows what its words cannot (the Show me draws 3 code fences as 3 pictures"
    )
    assert limits.BIG_RULING in skipped["reason"]
    assert skipped["reason"].endswith(f") in the body of {URL}")
    diagram = FIX_BODY + "\n## Show me\n" + SNIPPETS_SHOW_ME.split("no dialog.\n", 1)[1]
    monkeypatch.setattr(card, "run", answering(pr_json(body=diagram)))
    assert [drawn["number"] for drawn in ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]] == [42]


def test_a_tap_card_missing_two_sections_names_both_in_one_refusal(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## What you'll hate most")[0]
    monkeypatch.setattr(card, "run", answering(pr_json(body=body)))
    [skipped] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["skipped"]
    assert skipped["reason"].startswith(
        f"its tap card needs a \"What you'll hate most\" section and a \"His words, step by step\" list naming his order"
        f" in the body of {URL}. No order of his names {URL}: "
    )
    assert skipped["reason"].endswith("Never edit its body to quote his words for another change")
    assert [event["kind"] for event in events(drive)] == ["refused"]


def test_a_tap_card_asked_afresh_needs_a_summary_while_one_already_drawn_redraws_without_it(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json(body=FIX_BODY.replace(f"## Summary\n{SUMMARY}\n\n", ""))))
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert drawn["card"]["prs"][0]["summary"] == ""
    key = "Forge-AI/monorepo#42"
    assert card.tap_refusal(card.drive_prs(), [key], card.read_all([key])) == (
        "its tap card needs a \"Summary\" section first, a sentence or two naming what the PR changes by its function,"
        f" table, flag or service in the body of {URL}"
    )


def test_a_pr_waiting_on_his_open_design_card_draws_no_approve(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json()))
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["waits"] = ["demo:j1-7-a"]
    reason = "it waits on demo:j1-7-a, so it draws no Approve until each is answered or done"
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]}) == {
        "cards": [],
        "skipped": [{"repo": "Forge-AI/monorepo", "number": 42, "reason": reason}],
    }


VERDICT = {"job": "j9", "at": "2026-11-09T09:00:00-08:00", "why": "Releasable alone needs #7 live first", "redrawn": False}


def test_a_pr_a_reader_called_not_ready_draws_no_fresh_card_and_his_open_one_loses_its_approve(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json(rollup=PASSING, review="APPROVED")))
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["not_ready"] = VERDICT
    [skipped] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["skipped"]
    assert skipped["reason"].startswith("reader j9 called it NOT READY at ")
    assert skipped["reason"].endswith(
        ": Releasable alone needs #7 live first, so it draws no Approve until a later reader's READY names it: start a reader on its head"
    )
    tap_card = {"url": "https://acme.slack.com/archives/D123/p1794029589141509", "prs": [URL], "asked": "2026-11-09T08:00:00-08:00", "card": {"title": "Fix: approve retry flaky uploads?"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap_card]})
    view.refresh_cards(drive)
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert (drawn["card"]["status"], "options" in drawn["card"]) == ("info", False)
    assert drawn["card"]["prs"][0]["blocks"].startswith("A reader's check found it not ready: Releasable alone needs #7 live first.")
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["not_ready"] = None
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert (drawn["card"]["status"], drawn["card"]["options"]) == ("needs_you", ["Approve", "Hold"])


def test_a_redraw_of_a_posted_card_that_no_longer_asks_his_tap_draws_the_reason_that_stands_now(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", answering(pr_json(rollup=PASSING, review="APPROVED")))
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": []})
    view.refresh_cards(drive)
    for why in ("Releasable alone needs #7 live first", "The restore still copies at 0 B/s"):
        with state.edit(drive) as data:
            data["prs"]["Forge-AI/monorepo#42"]["not_ready"] = {**VERDICT, "why": why}
        [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}], "redraw": True})["cards"]
        assert (drawn["card"]["status"], "options" in drawn["card"]) == ("info", False)
        assert drawn["card"]["prs"][0]["blocks"].startswith(f"A reader's check found it not ready: {why}.")


def test_a_pr_whose_wait_holds_redraws_his_open_card_with_its_buttons_saying_what_it_waits_on(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "run", lambda cmd: json.dumps(pr_json()))
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["waits"] = ["release:dashboard:Forge-AI/monorepo#7"]
    tap_card = {"url": "https://acme.slack.com/archives/D123/p1794029589141509", "prs": [URL], "asked": "2026-11-09T08:00:00-08:00", "card": {"title": "Fix: approve retry flaky uploads?"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap_card]})
    view.refresh_cards(drive)
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert (drawn["card"]["status"], drawn["card"]["options"], drawn["card"]["waits"]) == ("needs_you", ["Approve", "Hold"], "merge:Forge-AI:monorepo:42")
    release = "the dashboard release carrying [retry flaky uploads](https://github.com/Forge-AI/monorepo/pull/7) is out"
    assert drawn["card"]["prs"][0]["lands"] == f"you approve, CI passes and {release}"
    assert card.render(pr_json(rollup=PASSING), entry(tap="Approve"), (), (release,))["prs"][0]["blocks"] == f"Lands once {release}"


@pytest.mark.parametrize(
    "words",
    [
        "",
        "None yet: no message or card pick of his names this bug. It reached the bug lane from a teammate's report.",
        "- none",
    ],
)
def test_a_tap_card_whose_his_words_name_no_order_of_his_is_refused(
    drive: str, monkeypatch: pytest.MonkeyPatch, words: str
) -> None:
    body = FIX_BODY.split("## His words, step by step")[0] + f"## His words, step by step\n{words}\n"
    monkeypatch.setattr(card, "run", answering(pr_json(body=body)))
    [skipped] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["skipped"]
    assert skipped["reason"].startswith(
        f"its tap card needs a \"His words, step by step\" list naming his order in the body of {URL}."
        f" No order of his names {URL}: "
    )
    assert [event["kind"] for event in events(drive)] == ["refused"]


def test_a_tap_card_with_no_his_words_section_is_refused(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## His words, step by step")[0]
    monkeypatch.setattr(card, "run", answering(pr_json(body=body)))
    assert ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"] == []


def test_a_drive_tooling_pr_whose_his_words_say_none_draws_its_card_with_approve(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## His words, step by step")[0] + "## His words, step by step\nNone: drive tooling.\n"
    monkeypatch.setattr(card, "run", answering(pr_json(body=body)))
    assert cli.main(["order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a tooling PR lands on his Approve", "--drive", drive]) == 0
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["serves"] = "o1"
    reply = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})
    assert [(drawn["number"], drawn["card"]["status"]) for drawn in reply["cards"]] == [(42, "needs_you")]
    assert "refused" not in [event["kind"] for event in events(drive)]


def test_an_exempt_port_with_no_his_words_still_draws(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["exempt"] = "pure port"
    body = FIX_BODY.split("## His words, step by step")[0]
    monkeypatch.setattr(card, "run", answering(pr_json(title="Port: retry flaky uploads", body=body)))
    [drawn] = ask({"prs": [{"repo": "Forge-AI/monorepo", "number": 42}]})["cards"]
    assert drawn["card"]["status"] == "info"


def test_a_card_that_waits_on_no_tap_draws_without_what_he_will_hate_most(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = FIX_BODY.split("## What you'll hate most")[0]
    monkeypatch.setattr(card, "run", answering(pr_json(body=body, pr_state="MERGED")))
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


def test_a_refusal_and_a_tap_are_written_with_the_lane_hearing_their_order_then(drive: str) -> None:
    assert ask({"refused": [{"repo": "Forge-AI/monorepo", "number": 42, "reason": "card needs a title"}]}) == {"refused": 1}
    assert cli.main(["order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page", "--drive", drive]) == 0
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["order"] = "o1"
        cli.bind_subcoordinator(data, "Iris", {"session": "iris-1", "terminal": None, "cwd": "/work", "started": state.stamp()})
    answer = {"repo": "Forge-AI/monorepo", "number": 42, "waits": "", "label": "Approve", "action_ts": "", "source": "s"}
    assert ask({"answer": answer}) == {"ok": True}
    assert [(event["kind"], event["lane"]) for event in events(drive)] == [("refused", "root"), ("tap", "iris")]


def test_a_tap_goes_to_the_drive_whose_entry_for_the_pr_was_updated_last(drive: str) -> None:
    copy = state.new_drive("copy", "coordinator", "s2", "/work", "D123")
    copy["prs"] = {"Forge-AI/monorepo#42": {**entry(), "updated": "2026-11-11T00:20:00-08:00"}}
    state.create(copy)
    with state.edit(drive) as data:
        data["prs"]["Forge-AI/monorepo#42"]["updated"] = "2026-11-11T00:42:12-08:00"
    assert card.drive_prs()["Forge-AI/monorepo#42"][0] == drive
    with state.edit("copy") as data:
        data["prs"]["Forge-AI/monorepo#42"]["updated"] = "2026-11-11T00:43:00-08:00"
    assert card.drive_prs()["Forge-AI/monorepo#42"][0] == "copy"


def test_an_answer_on_a_drives_question_card_is_posted_as_its_one_answer_record(drive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[list[str], list[dict]]] = []
    ok = subprocess.CompletedProcess([], 0, "661500\n", "")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: posted.append((argv, json.loads(kwargs["input"]))) or ok)
    url = "https://acme.slack.com/archives/D123/p1794200000000100"
    tap = {"waits": f"{drive}:j3-661440", "label": "Ring", "url": url, "action_ts": "1794200001.000100", "source": "slack"}
    assert ask({"answer": tap}) == {"ok": True, "seq": "661500"}
    ask({"answer": tap | {"label": "neither, keep both", "other": True}})
    ask({"answer": tap | {"undone": True}})
    ask({"answer": tap | {"waits": f"{drive}:hier-1", "label": "Keep both until Friday", "other": True}})
    batch = ["cci", "post", "--drive", drive, "--lane", "communicator", "--batch", "-"]
    answer = {"kind": "answer", "to": [f"root@{drive}"], "url": url, "re": ["661440"]}
    assert posted == [
        (batch, [answer | {"text": "Ring", "attr": {"job": "j3"}}]),
        (batch, [answer | {"text": "neither, keep both", "attr": {"maps": "none", "job": "j3"}}]),
        (batch, [answer | {"text": "undone: Ring", "attr": {"job": "j3", "undone": "Ring"}}]),
        (batch, [{"kind": "answer", "to": [f"root@{drive}"], "url": url, "topic": "card:1794200000.000100", "text": "Keep both until Friday", "attr": {"maps": "none"}}]),
    ]
    assert events(drive) == []
    with pytest.raises(state.DriveError, match="step-3:merge is no drive's question card"):
        card.respond({"answer": tap | {"waits": "step-3:merge"}})


def test_answer_stores_his_tap_and_appends_a_tap_event(drive: str) -> None:
    answer = {
        "repo": "Forge-AI/monorepo",
        "number": 42,
        "waits": "merge:Forge-AI:monorepo:42",
        "label": "Approve",
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

    def gh(cmd: list[str]) -> str:
        if cmd[1] == "api":
            return stacked_gh(cmd)
        pr = json.loads(stacked_gh(cmd))
        pr["body"] += f"\n## Show me\n```mermaid\nflowchart TD\n  a --> b{cmd[3]}\n```\n"
        return json.dumps(pr)

    monkeypatch.setattr(card, "run", gh)
    [drawn] = ask(asks(41, 42))["cards"]
    assert [[Path(path).name for path in row["pictures"]] for row in drawn["card"]["prs"]] == [["Diagram"], ["Diagram (2)"]]


def test_a_stack_card_waiting_on_him_needs_the_stack_section(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "STACK_HEADING", "no such section")
    drawn = ask(asks(41, 42, 43))
    assert drawn["cards"] == []
    assert all("The stack" in skipped["reason"] for skipped in drawn["skipped"])


def test_a_stack_refusal_names_each_section_every_pr_body_lacks(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "STACK_HEADING", "no such section")
    stacked_gh = card.run

    def gh(cmd: list[str]) -> str:
        pr = json.loads(stacked_gh(cmd))
        if cmd[3] == "42":
            pr["body"] = pr["body"].replace("## What you'll hate most", "## Biggest worry")
        return json.dumps(pr)

    monkeypatch.setattr(card, "run", gh)
    drawn = ask(asks(41, 42, 43))
    reason = (
        f"its tap card needs a \"The stack\" section with the stack's one-line title then its summary in the body of"
        f" {stack_url(41)}; a \"What you'll hate most\" section in the body of {stack_url(42)}"
    )
    assert drawn["cards"] == []
    assert [skipped["reason"] for skipped in drawn["skipped"]] == [reason, reason, reason]


def test_a_stack_longer_than_a_card_holds_is_refused_whole(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "STACK_MOST", 2)
    drawn = ask(asks(41, 42, 43))
    assert drawn["cards"] == []
    assert len(drawn["skipped"]) == 3
    assert all(skipped["reason"].startswith("its stack holds 3 PRs and a stack is at most 2,") for skipped in drawn["skipped"])


def test_exempt_and_yasyf_prs_are_not_counted_toward_a_stacks_cap(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "STACK_MOST", 2)
    with state.edit("demo") as data:
        for number in (41, 42, 43):
            data["prs"][f"Forge-AI/monorepo#{number}"]["exempt"] = "test-only"
    [drawn] = ask(asks(41, 42, 43))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(41), stack_url(42), stack_url(43)]
    own = [(pr_json(url=f"https://github.com/yasyf/cc-skills/pull/{number}"), entry(wants_tap=False), ()) for number in (1, 2, 3)]
    assert card.refusal(own, card.drawn_card(own), set()) == ""


def test_a_card_drawn_after_its_body_is_fixed_clears_the_refusal(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card, "STACK_HEADING", "no such section")
    ask(asks(41, 42, 43))
    assert all(state.load("demo")["prs"][f"Forge-AI/monorepo#{n}"]["refused"] for n in (41, 42, 43))
    monkeypatch.setattr(card, "STACK_HEADING", "the stack")
    assert len(ask(asks(41, 42, 43))["cards"]) == 1
    assert not any(state.load("demo")["prs"][f"Forge-AI/monorepo#{n}"].get("refused") for n in (41, 42, 43))


def test_a_stack_with_one_pr_a_reader_called_not_ready_draws_no_approve_for_the_stack(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    ask(asks(41, 42, 43))
    shown = {"url": "https://acme.slack.com/archives/D123/p1794029589141509", "prs": [stack_url(n) for n in (41, 42, 43)], "asked": "2026-11-09T08:00:00-08:00", "card": {"title": "New: the orders table moves"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [shown]})
    view.refresh_cards("demo")
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#42"]["not_ready"] = VERDICT
    [drawn] = ask(asks(41, 42, 43))["cards"]
    assert (drawn["card"]["status"], "options" in drawn["card"]) == ("info", False)


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
    monkeypatch.setattr(card, "run", lambda cmd: stacked_gh(cmd) if cmd[1] == "api" else json.dumps(json.loads(stacked_gh(cmd)) | {"state": "MERGED"}))
    [drawn] = ask(asks(41, 42, 43))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(41), stack_url(42), stack_url(43)]
    assert drawn["card"]["status"] == "done"


def test_a_pr_added_to_the_stack_later_gets_its_own_card(stacked: list[list[str]]) -> None:
    ask(asks(41, 42, 43))
    [drawn] = ask(asks(44))["cards"]
    assert [row["url"] for row in drawn["card"]["prs"]] == [stack_url(44)]
    assert drawn["card"]["waits"] == "merge:Forge-AI:monorepo:44"
    assert "stack" not in state.load("demo")["prs"]["Forge-AI/monorepo#44"]


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


def test_a_chain_that_cuts_through_a_stored_stack_keeps_every_pr_on_one_card(stacked: list[list[str]]) -> None:
    ask(asks(41, 42))
    with state.edit("demo") as data:
        for n in (43, 44):
            data["prs"][f"Forge-AI/monorepo#{n}"]["stack"] = ["Forge-AI/monorepo#43", "Forge-AI/monorepo#44"]
    drawn = ask(asks(41, 42, 43))["cards"]
    rows = [row["url"] for each in drawn for row in each["card"]["prs"]]
    assert sorted(rows) == sorted(set(rows))
    assert state.load("demo")["prs"]["Forge-AI/monorepo#41"]["stack"] == ["Forge-AI/monorepo#41", "Forge-AI/monorepo#42"]


def test_a_grown_stack_refused_keeps_the_stack_it_had(stacked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    ask(asks(41, 42, 43))
    monkeypatch.setattr(card, "STACK_HEADING", "no such section")
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
