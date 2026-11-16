import io
import json

import pytest
from lane import voice


def test_the_sender_queues_in_the_lane_channel_and_thread_and_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
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
                "channel_id": "C0RUNPAGE",
                "thread_ts": "1794863488.099589",
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
    ],
)
def test_a_pr_the_lane_holds_draws_as_its_number_and_words(typed: str, drawn: str) -> None:
    assert voice.linked(typed, PRS) == drawn


def test_importing_voice_makes_it_lane_say_s_sender() -> None:
    from lane import say

    assert say.SENDER is voice.send


def test_his_message_in_a_lane_channel_reaches_that_lane_by_its_channel_and_thread(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import store, verbs

    (tmp_path / "demo").mkdir()
    lanes = {"run-page": {"channel": "C0RUN", "thread": None}, "v3-a": {"channel": "C0SHARED", "thread": "1.1"}, "v3-b": {"channel": "C0SHARED", "thread": "2.2"}}
    monkeypatch.setattr(store, "root", lambda: tmp_path)
    monkeypatch.setattr(store, "load_all", lambda drive: lanes)
    heard = []
    monkeypatch.setattr(verbs, "message", lambda drive, lane_id, words, link, ts: heard.append((drive, lane_id, words, ts)) or 1)
    assert voice.heard("C0RUN", None, "make this a prio lane", None, "9.9") == "run-page: 1 new"
    assert voice.heard("C0SHARED", "2.2", 'tapped "Approve"', None, "9.8") == "v3-b: 1 new"
    assert voice.heard("C0NONE", None, "hi", None, "9.7") == "no one lane holds C0NONE/: 0 do"
    assert heard == [("demo", "run-page", "make this a prio lane", "9.9"), ("demo", "v3-b", 'tapped "Approve"', "9.8")]
