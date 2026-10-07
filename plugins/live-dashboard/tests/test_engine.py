from __future__ import annotations

import json
from concurrent.futures import Executor, Future
from pathlib import Path

import pytest
from livedash import Kv, check, layout, registry
from livedash.scheduler import InlineExecutor, Scheduler

COMPONENTS = '''
from pathlib import Path

from livedash import Context, Kv, RateLimited, Tile, Tiles, component

CALLS = []


@component("counter", "Counter", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def counter(ctx: Context, *, start: int = 0) -> Tiles:
    CALLS.append(start)
    return Tiles([Tile("calls", start + len(CALLS))])


@component("broken", "Broken", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def broken(ctx: Context) -> Kv:
    raise RuntimeError("boom")


@component("limited", "Limited", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def limited(ctx: Context) -> Kv:
    raise RateLimited("exit 1: API rate limit exceeded")


@component("leaky", "Leaky", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def leaky(ctx: Context) -> Kv:
    return Kv({"token": "hunter2hunter2"})


@component("wrong", "Wrong", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def wrong(ctx: Context) -> Kv:
    return Tiles([])


@component("spills", "Spills", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def spills(ctx: Context) -> Kv:
    raise ValueError("auth failed for hunter2hunter2 and sk-abcdefghijklmnopqrstuv")


@component("chatty", "Chatty", question="Does the test card answer its one question?", reads=["a test fixture"], every="1m")
def chatty(ctx: Context) -> Kv:
    print("debugging")
    return Kv({"ok": 1})


@component("needs", "Needs", question="Does the test card answer its one question?", reads=["a test fixture"], every="manual")
def needs(ctx: Context, *, ledger: str, files: Path, prs: list[int] = []) -> Kv:
    return Kv({"ledger": ledger, "files": str(files), "prs": len(prs)})
'''
LAYOUT = """title: Engine
sections:
  - title: Counters
    components:
      - {use: local.counter, id: a}
      - {use: local.counter, id: b, every: 15s}
      - {use: local.counter, id: c, with: {start: 10}}
  - title: Failing
    collapsed: true
    components:
      - {use: local.broken}
      - {use: local.limited}
      - {use: local.leaky}
      - {use: local.wrong}
      - {use: local.spills}
      - use: local.needs
        with:
          files: runs/latest.json
"""
FACTS = {"id": "engine", "ledger": "L1", "packs": {}}
ENV = {"MY_TOKEN": "hunter2hunter2"}


class Pending(Executor):
    def submit(self, fn, /, *args, **kwargs) -> Future:
        return Future()


@pytest.fixture
def board(tmp_path) -> Path:
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "engine_cards.py").write_text(COMPONENTS)
    (tmp_path / "layout.yaml").write_text(LAYOUT)
    (tmp_path / "context.json").write_text(json.dumps(FACTS))
    registry.load_local(tmp_path)
    yield tmp_path
    registry.forget(registry.LOCAL_PACKAGE)


def scheduler(board: Path, clock: list[float], executor: Executor | None = None) -> tuple[Scheduler, layout.Layout]:
    built = layout.load(board, FACTS)
    found = Scheduler(board, FACTS, clock=lambda: clock[0], executor=executor or InlineExecutor(), env=ENV)
    found.apply(built.cards)
    return found, built


def run(found: Scheduler) -> None:
    found.tick()
    found.tick()


def by_id(found: Scheduler, built: layout.Layout) -> dict[str, dict]:
    return {envelope["id"]: envelope for envelope in found.envelopes(built.cards)}


def test_cards_with_the_same_use_and_with_share_one_run_at_the_fastest_cadence(board):
    clock = [0.0]
    found, built = scheduler(board, clock)
    run(found)
    cards = by_id(found, built)
    assert cards["a"]["payload"] == cards["b"]["payload"] != cards["c"]["payload"]
    assert found.instance_of("a") is found.instance_of("b")
    assert found.instance_of("a").every == 15
    assert sorted(registry.REGISTRY["local.counter"].fn.__globals__["CALLS"]) == [0, 10]


def test_a_failure_backs_off_doubling_up_to_four_cadences(board):
    clock = [0.0]
    found, built = scheduler(board, clock)
    run(found)
    broken = found.instance_of("local.broken")
    assert (broken.error, broken.due) == ("RuntimeError: boom", 120.0)
    for now, due in ((120.0, 360.0), (360.0, 600.0)):
        clock[0] = now
        run(found)
        assert broken.due == due
    assert by_id(found, built)["local.broken"]["status"] == "error"


def test_a_rate_limit_waits_five_minutes(board):
    clock = [0.0]
    found, _ = scheduler(board, clock)
    run(found)
    limited = found.instance_of("local.limited")
    assert (limited.error, limited.due) == ("exit 1: API rate limit exceeded", 300.0)


def test_a_payload_carrying_a_secret_or_the_wrong_type_is_dropped(board):
    clock = [0.0]
    found, built = scheduler(board, clock)
    run(found)
    cards = by_id(found, built)
    assert cards["local.leaky"]["error"] == "ValueError: secret-shaped value (MY_TOKEN); the payload was dropped"
    assert cards["local.leaky"]["payload"] is None
    assert cards["local.wrong"]["error"] == "TypeError: local.wrong returned Tiles, not the Kv its signature names"
    assert not (board / "cache" / "local.leaky.json").exists()
    assert cards["local.spills"]["error"] == "ValueError: auth failed for <MY_TOKEN> and <redacted>"


def test_params_bind_from_with_then_facts_then_defaults(board):
    clock = [0.0]
    found, built = scheduler(board, clock)
    found.run_once("local.needs")
    assert found.payload_of("local.needs").pairs == {"ledger": "L1", "files": str(board / "runs" / "latest.json"), "prs": 0}


def test_a_card_goes_stale_after_two_cadences_without_a_good_run(board):
    clock = [0.0]
    found, built = scheduler(board, clock)
    run(found)
    clock[0] = 121.0
    assert by_id(found, built)["c"]["status"] == "stale"


def test_a_run_past_its_timeout_reads_hung(board):
    clock = [0.0]
    found, built = scheduler(board, clock, Pending())
    found.tick()
    clock[0] = 31.0
    found.tick()
    assert by_id(found, built)["c"]["status"] == "hung"


def test_a_restart_serves_the_cached_payload_until_the_next_run(board):
    clock = [0.0]
    found, _ = scheduler(board, clock)
    run(found)
    clock[0] = 10.0
    again, built = scheduler(board, clock)
    cards = by_id(again, built)
    assert cards["c"]["status"] == "ok" and cards["c"]["payload"]["tiles"][0]["value"] == 12
    assert again.instance_of("c").due == 60.0


def test_a_manual_card_runs_once_and_again_only_on_refresh(board):
    clock = [0.0]
    found, _ = scheduler(board, clock)
    run(found)
    assert found.instance_of("local.needs").due == float("inf")
    assert found.refresh("local.needs") and found.instance_of("local.needs").due == 0.0
    assert not found.refresh("nope")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda text: text.replace("id: a}", "id: a, colour: red}"), "layout.yaml:5: card has unknown key colour"),
        (lambda text: text.replace("id: b,", "id: a,"), "layout.yaml:6: card id 'a' repeats line 5"),
        (lambda text: text.replace("local.broken", "local.nothing"), "layout.yaml:11: unknown component 'local.nothing'"),
        (lambda text: text.replace("      with:\n          files: runs/latest.json\n", ""), "layout.yaml:16: local.needs needs files"),
        (lambda text: text.replace("{start: 10}", "{start: ten}"), "layout.yaml:7: start must be int, not 'ten'"),
        (lambda text: text.replace("every: 15s", "every: 10s"), "layout.yaml:6: every must be one of"),
        (lambda text: text.replace("every: 15s", "every: [1m]"), "layout.yaml:6: every must be one of"),
        (lambda text: text.replace("id: a}", "id: feature/api}"), "layout.yaml:5: card id 'feature/api' must be"),
        (lambda text: text.replace("id: a}", "id: ../outside}"), "layout.yaml:5: card id '../outside' must be"),
        (lambda text: text + "colour: red\n", "layout.yaml:1: unknown top-level key colour"),
    ],
)
def test_layout_defects_name_their_line(board, edit, message):
    path = board / "layout.yaml"
    path.write_text(edit(LAYOUT))
    with pytest.raises(layout.LayoutError) as raised:
        layout.load(board, FACTS)
    assert raised.value.where(path).startswith(message)


def test_a_local_module_that_fails_to_load_breaks_only_its_cards(board):
    (board / "components" / "engine_cards.py").write_text("raise ImportError('no such thing')\n")
    registry.load_local(board)
    built = layout.load(board, FACTS)
    assert [card.error for card in built.cards][:1] == ["engine_cards.py failed to load: ImportError: no such thing"]


def test_check_reports_every_defect_one_line_each(board):
    (board / "layout.yaml").write_text(LAYOUT.replace("      - {use: local.wrong}\n", "      - {use: local.wrong}\n      - {use: local.chatty}\n"))
    found = check.defects(board, None, True, ENV)
    assert "local.broken (local.broken, layout.yaml:11): RuntimeError: boom" in found
    assert "local.leaky (local.leaky, layout.yaml:13): secret-shaped value (MY_TOKEN)" in found
    assert "local.wrong (local.wrong, layout.yaml:14): returned Tiles, not the Kv its signature names" in found
    assert "local.chatty (local.chatty, layout.yaml:15): printed 10 characters to stdout; a component prints nothing" in found
    assert check.defects(board, "c", True, ENV) == []
    assert check.defects(board, None, False, ENV) == []


@pytest.mark.parametrize(
    ("define", "message"),
    [
        (lambda: registry.component("Bad_Id", "x", question="Does the test card answer its one question?", reads=["a test fixture"]), "lowercase words"),
        (lambda: registry.component("ok", "x", question="Does the test card answer its one question?", reads=["a test fixture"], every="10s"), "every='10s'"),
        (lambda: registry.component("ok", "x", question="Does the test card answer its one question?", reads=["a test fixture"])(lambda ctx: Kv({})), "must annotate its return"),
        (lambda: registry.component("ok", "x", question="Does the test card answer its one question?", reads=["a test fixture"])(positional), "must be keyword-only"),
        (lambda: registry.component("ok", "x", question="Does the test card answer its one question?", reads=["a test fixture"])(typed), "parameter rows has type"),
        (lambda: registry.component("ok", "x", question="Does the test card answer its one question?", reads=["a test fixture"])(mixed), "parameter limit has type"),
        (lambda: registry.component("ok", "x", question="A statement, not a question", reads=["a test fixture"]), "ending in \\?"),
        (lambda: registry.component("ok", "x", question="Does the test card answer its one question?", reads=[]), "each source it reads"),
    ],
)
def test_the_decorator_refuses_components_the_engine_cannot_bind(define, message):
    with pytest.raises(registry.BindError, match=message):
        define()


def positional(ctx, ledger: str) -> Kv:
    return Kv({})


def typed(ctx, *, rows: list[dict]) -> Kv:
    return Kv({})


def mixed(ctx, *, limit: int | float | None = None) -> Kv:
    return Kv({})


def test_a_mapping_parameter_takes_only_scalar_values(tmp_path):
    names = dict[str, str] | None
    assert registry.coerce("names", {"103": 101}, names, tmp_path) == {"103": "101"}
    for bad in ({"103": None}, {"103": [1]}, {"103": True}):
        with pytest.raises(registry.BindError, match="mapping of strings"):
            registry.coerce("names", bad, names, tmp_path)
