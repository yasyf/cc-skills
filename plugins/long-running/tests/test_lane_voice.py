import io
import json
from pathlib import Path

import pytest
from lane import voice


def test_an_owner_say_queues_in_its_lane_channel_and_returns_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice, "lane_file", lambda drive, lane_id: tmp_path / f"{drive}-{lane_id}.json")
    (tmp_path / "demo-run-page.json").write_text(json.dumps({"channel": "C0RUNPAGE", "thread_ts": "1794863488.099589"}))
    sent = []

    def answered(request, timeout):
        sent.append((request.full_url, json.loads(request.data), timeout))
        return io.StringIO(json.dumps({"id": "a1b2c3"}))

    monkeypatch.setattr(voice.urllib.request, "urlopen", answered)
    assert voice.say(Path("/plugin"), "demo", "run-page", {"text": "Right, v3 doesn't need the conversion."}) == "QUEUED a1b2c3"
    assert sent == [
        (
            voice.VOICE_URL,
            {
                "lane": "run-page",
                "channel_id": "C0RUNPAGE",
                "thread_ts": "1794863488.099589",
                "text": "Right, v3 doesn't need the conversion.",
                "rules": "/plugin/skills/long-running/reference/voice.md",
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
