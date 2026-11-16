import json
from collections.abc import Callable
from pathlib import Path

import pytest
from drive import place, spend, state

Run = Callable[..., tuple[int, str]]
MILLION = 1_000_000


def turn(at: str, message: str, tokens: int, model: str = "claude-opus-5-5") -> str:
    usage = {"input_tokens": tokens, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    return json.dumps({"type": "assistant", "timestamp": at, "message": {"id": message, "model": model, "usage": usage}})


def test_a_transcript_prices_each_message_once_today_alone_and_reads_on_from_where_it_ended(tmp_path: Path) -> None:
    day = state.now().date().isoformat()
    today = state.stamp(state.now())
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join([turn("2020-01-01T00:00:00Z", "m0", MILLION), turn(today, "m1", MILLION), turn(today, "m1", MILLION), '{"type": "assistant", "usage"', turn(today, "m2", MILLION, "claude-fable-5-2")]) + "\n")
    held = [0, None]
    assert spend.read_since(path, held, day) == pytest.approx(12.0)
    with path.open("a") as more:
        more.write(turn(today, "m3", MILLION, "claude-sonnet-5-9") + "\n" + turn(today, "m4", MILLION))
    assert spend.read_since(path, held, day) == pytest.approx(2.0)
    assert held == [path.stat().st_size - len(turn(today, "m4", MILLION)), "m3"]


def test_a_message_is_priced_at_its_models_list_price_with_cache_reads_at_five_percent_and_hour_writes_at_twice_input() -> None:
    usage = {"input_tokens": MILLION, "output_tokens": MILLION, "cache_creation_input_tokens": 2 * MILLION, "cache_creation": {"ephemeral_1h_input_tokens": MILLION}, "cache_read_input_tokens": 20 * MILLION}
    assert spend.priced("claude-opus-5-5", usage) == pytest.approx(4.0 * (1 + 1.25 + 2.0 + 1.0) + 20.0)
    assert spend.priced(spend.SYNTHETIC, usage) == 0.0
    assert (spend.money(3750), spend.money(15000), spend.money(950.4)) == ("$3.8k", "$15k", "$950")


def test_a_job_whose_project_hit_its_daily_budget_waits_for_tomorrow_unless_a_priority_order_or_its_own_mark_serves_it(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    (state.root() / place.CAPS_FILE).write_text(json.dumps({"projects": {"Releases": 100}}))
    monkeypatch.setattr(spend, "kept", lambda moment: {"at": None, "offsets": {}, "usd": {f"{demo}/Releases": 150.0, "other/Releases": 50.0}, "told": []})
    data = state.load(demo)
    assert place.admission(data, "j1", "sprite", state.now()) == "Releases hit today's $100 budget, at $200 spent, set by caps.json"
    with state.edit(demo) as data:
        place.queue_sprite(data["jobs"]["j1"], state.now())
    assert place.sprite_line(state.load(demo)) == []
    assert run("job", "set", "j1", "--priority")[0] == 0
    data = state.load(demo)
    assert (place.admission(data, "j1", "sprite", state.now()), place.sprite_line(data)) == (None, ["j1"])


def test_drive_tooling_hits_its_share_of_the_days_total_counted_over_every_drive_and_his_line_says_it_in_dollars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spend, "kept", lambda moment: {"at": None, "offsets": {}, "usd": {"a/Drive tooling": 3000.0, "b/Drive tooling": 900.0, "a/Iris": 4900.0}, "told": []})
    assert spend.over_project(spend.TOOLING_PROJECT, state.now()) == "Drive tooling hit today's $3.8k budget, at $3.9k spent, set by caps.json"
    assert spend.over_project("Iris", state.now()) is None
    assert spend.his_line(spend.TOOLING_PROJECT) == "Drive tooling hit today's $3.8k budget; non-urgent tooling waits until tomorrow."
