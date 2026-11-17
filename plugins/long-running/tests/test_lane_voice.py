import io
import json

import pytest
from lane import voice


def test_the_sender_queues_at_the_lane_channel_root_never_its_old_home_thread_and_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = {"id": "run-page", "channel": "C0RUNPAGE", "thread": "1794863488.099589", "prs": []}
    sent = []

    def answered(request, timeout):
        sent.append((request.full_url, json.loads(request.data), timeout))
        return io.StringIO(json.dumps({"id": "a1b2c3"}))

    monkeypatch.setattr(voice.urllib.request, "urlopen", answered)
    assert voice.send("demo", lane, "Right, v3 doesn't need the conversion.") == "QUEUED a1b2c3"
    assert sent == [
        (
            voice.VOICE_URL,
            {
                "lane": "run-page",
                "drive": "demo",
                "channel_id": "C0RUNPAGE",
                "thread_ts": "",
                "text": "Right, v3 doesn't need the conversion.",
                "rules": str(voice.ROLE_PAGE),
                "asked": "",
            },
            voice.ENQUEUE_SECONDS,
        )
    ]


@pytest.mark.parametrize(("named", "owner"), [("demo/run-page", ("demo", "run-page")), (None, None)])
def test_only_a_session_started_as_a_lane_owner_takes_the_fast_path(monkeypatch: pytest.MonkeyPatch, named: str | None, owner: tuple[str, str] | None) -> None:
    if named:
        monkeypatch.setenv(voice.LANE_ENV, named)
    else:
        monkeypatch.delenv(voice.LANE_ENV, raising=False)
    assert voice.own_lane() == owner


PRS = [{"repo": "Forge-AI/monorepo", "number": 53430, "words": "reads old journals"}, {"repo": "Forge-AI/monorepo", "number": 53305}, {"repo": "yasyf/cc-skills", "number": 53305}]


@pytest.mark.parametrize(
    ("typed", "drawn"),
    [
        ("Reworking {pr:53430|v3 reads}.", "Reworking <https://github.com/Forge-AI/monorepo/pull/53430|#53430 v3 reads>."),
        ("{pr:53430} lands first", "<https://github.com/Forge-AI/monorepo/pull/53430|#53430 reads old journals> lands first"),
        ("{pr:yasyf/cc-skills#53305|the hook}", "<https://github.com/yasyf/cc-skills/pull/53305|#53305 the hook>"),
        ("{pr:53305} is two PRs", "{pr:53305} is two PRs"),
        ("{pr:999} is none of the lane's", "{pr:999} is none of the lane's"),
        ("{pr:Forge-AI/monorepo#56399|two new tasks columns}", "<https://github.com/Forge-AI/monorepo/pull/56399|#56399 two new tasks columns>"),
        ("Merged <https://github.com/Forge-AI/monorepo/pull/53430|#53430>.", "Merged <https://github.com/Forge-AI/monorepo/pull/53430|#53430 reads old journals>."),
        ("Merged #53430, not #999.", "Merged <https://github.com/Forge-AI/monorepo/pull/53430|#53430 reads old journals>, not #999."),
        ("<https://x.example/a|see #53430>", "<https://x.example/a|see #53430>"),
    ],
)
def test_a_pr_the_lane_holds_draws_as_its_number_and_words(typed: str, drawn: str) -> None:
    assert voice.linked(typed, PRS) == drawn


def test_importing_voice_makes_it_lane_say_s_sender() -> None:
    from lane import say

    assert say.SENDER is voice.send


def test_his_message_reaches_its_lane_by_thread_and_every_lane_in_the_channel_when_unclear(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import store, verbs

    root = tmp_path / "lanes"
    (root / "demo").mkdir(parents=True)
    lanes = {"run-page": {"channel": "C0RUN", "thread": None}, "v3-a": {"channel": "C0SHARED", "thread": "1.1"}, "v3-b": {"channel": "C0SHARED", "thread": "2.2"}}
    monkeypatch.setattr(store, "root", lambda: root)
    monkeypatch.setattr(store, "load_all", lambda drive: lanes)
    present = []
    monkeypatch.setattr(store, "heard", lambda drive, asks: present.append((drive, asks)))
    heard = []
    monkeypatch.setattr(verbs, "message", lambda drive, lane_id, words, link, ts: heard.append((lane_id, ts)) or 1)
    assert voice.heard("C0RUN", None, "make this a prio lane", None, "9.9") == "run-page: 1 new"
    assert voice.heard("C0SHARED", "2.2", 'tapped "Approve"', None, "9.8") == "v3-b: 1 new"
    assert voice.heard("C0SHARED", None, "both of these", None, "9.7") == "v3-a: 1 new; v3-b: 1 new"
    assert voice.heard("C0SHARED", "3.3", "an older thread", None, "9.6") == "v3-a: 1 new; v3-b: 1 new"
    assert voice.heard("D0DM", None, 'tapped "Approve"', "https://x/p1", "9.5") == "no lane holds D0DM; his presence is kept"
    assert present == [("demo", [{"ts": "9.5", "link": "https://x/p1"}])]
    assert heard == [("run-page", "9.9"), ("v3-b", "9.8"), ("v3-a", "9.7"), ("v3-b", "9.7"), ("v3-a", "9.6"), ("v3-b", "9.6")]


@pytest.mark.parametrize(
    ("asks", "thread"),
    [
        ([], ""),
        ([{"ts": "1794870764.063919", "link": "https://x.slack.com/archives/C1/p1794870764063919"}], "1794870764.063919"),
        ([{"ts": "1794870764.063919", "link": "https://x.slack.com/archives/D9/p1794870764063919"}], ""),
        ([{"ts": "1794870764.063919", "link": "https://x.slack.com/archives/C1/p1794870764063919", "replied": "QUEUED a1"}], "1794870764.063919"),
        ([{"ts": "1794870764.063919", "link": "https://x.slack.com/archives/C1/p1794870764063919", "replied": "QUEUED a1", "done": "2026-11-16T23:50:00Z"}], ""),
        ([{"ts": "1.1", "link": "l"}, {"ts": "1794871000.000100", "link": "https://x.slack.com/archives/C1/p1794871000000100?thread_ts=1794851267.832189&cid=C1"}], "1794851267.832189"),
    ],
)
def test_every_post_about_his_newest_ask_stays_in_its_thread_until_it_is_done_and_news_goes_at_the_root(asks: list[dict], thread: str) -> None:
    assert voice.asked_thread({"channel": "C1", "asks": asks}) == thread


@pytest.mark.parametrize(
    ("title", "words"),
    [
        ("Fix: litestream-next takes no snapshot of a forked child at its base TXID, now on ssql-litestream", "litestream-next takes no snapshot of a forked child…"),
        ("feat(cc-slack): SandDB reads its own bucket", "SandDB reads its own bucket"),
        ("New: a sync ends each LTX file", "a sync ends each LTX file"),
    ],
)
def test_a_title_reads_as_a_few_words(title: str, words: str) -> None:
    assert voice.short(title) == words


def test_a_lane_pr_with_no_words_takes_them_from_its_title() -> None:
    rows = [{"repo": "Forge-AI/monorepo", "number": 57630}, {"repo": "Forge-AI/monorepo", "number": 1, "words": "own words"}]
    seen = {"Forge-AI/monorepo#57630": {"title": "Fix: sandbox cluster access for the admin and break-glass roles"}, "Forge-AI/monorepo#1": {"title": "ignored"}, "Forge-AI/monorepo#57908": {"title": "Fix: release-tool promotions run again"}}
    assert voice.linked("Merged #57630, {pr:1} and #57908.", voice.titled(rows, seen)) == (
        "Merged <https://github.com/Forge-AI/monorepo/pull/57630|#57630 sandbox cluster access for the admin and break-glass…>,"
        " <https://github.com/Forge-AI/monorepo/pull/1|#1 own words> and <https://github.com/Forge-AI/monorepo/pull/57908|#57908 release-tool promotions run again>."
    )
