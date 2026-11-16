import json
from collections.abc import Callable
from pathlib import Path

import pytest
from drive import place, spend, state

Run = Callable[..., tuple[int, str]]


def turn(at: str, message: str, tokens: int) -> str:
    usage = {"input_tokens": tokens, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    return json.dumps({"type": "assistant", "timestamp": at, "message": {"id": message, "usage": usage}})


def test_a_transcript_counts_each_message_once_today_alone_and_reads_on_from_where_it_ended(tmp_path: Path) -> None:
    day = state.now().date().isoformat()
    today = state.stamp(state.now())
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join([turn("2020-01-01T00:00:00Z", "m0", 7), turn(today, "m1", 5), turn(today, "m1", 5), '{"type": "assistant", "usage"', turn(today, "m2", 3)]) + "\n")
    held = [0, None]
    assert spend.read_since(path, held, day) == 8
    with path.open("a") as more:
        more.write(turn(today, "m3", 4) + "\n" + turn(today, "m4", 9))
    assert spend.read_since(path, held, day) == 4
    assert held == [path.stat().st_size - len(turn(today, "m4", 9)), "m3"]


def test_a_job_whose_project_spent_its_daily_budget_waits_for_tomorrow_unless_a_priority_order_serves_it(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    (state.root() / place.CAPS_FILE).write_text(json.dumps({"projects": {"Releases": 100}}))
    monkeypatch.setattr(spend, "kept", lambda moment: {"at": None, "offsets": {}, "tokens": {f"{demo}/Releases": 150, "other/Releases": 50}, "told": []})
    data = state.load(demo)
    assert place.admission(data, "j1", "sprite", state.now()) == "Releases spent 200 tokens today, at its daily budget of 100 set by caps.json"
    with state.edit(demo) as data:
        place.queue_sprite(data["jobs"]["j1"], state.now())
    assert place.sprite_line(state.load(demo)) == []
    assert run("order", "set", "o1", "--priority")[0] == 0
    data = state.load(demo)
    assert (place.admission(data, "j1", "sprite", state.now()), place.sprite_line(data)) == (None, ["j1"])


def test_drive_tooling_spends_its_share_of_the_days_total_counted_over_every_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    (state.root() / place.CAPS_FILE).write_text(json.dumps({"daily_tokens": 1000, "tooling_share": 0.25}))
    monkeypatch.setattr(spend, "kept", lambda moment: {"at": None, "offsets": {}, "tokens": {"a/Drive tooling": 200, "b/Drive tooling": 60, "a/Iris": 900}, "told": []})
    assert spend.over_project(spend.TOOLING_PROJECT, state.now()) == "Drive tooling spent 260 tokens today, at its daily budget of 250 set by caps.json"
    assert spend.over_project("Iris", state.now()) is None
