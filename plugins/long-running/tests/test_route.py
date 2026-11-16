from conftest import Jev
from drive import agents, route, settled, state


def test_the_fail_safe_fresh_handler_is_the_first_route_jev_leans_to() -> None:
    assert list(route.routes(state.new_drive("demo", "coordinator", "s", "/tmp")))[:2] == [route.NEW, route.COMMUNICATOR]


def test_a_drive_with_no_communicator_offers_no_coordinator_route() -> None:
    assert route.COORDINATOR not in route.routes(state.new_drive("demo", "coordinator", "s", "/tmp"))


def test_no_reading_from_jev_routes_to_a_fresh_handler(jev: Jev) -> None:
    jev.answer = None
    chosen, why, away, _ = route.picked(state.new_drive("demo", "coordinator", "s", "/tmp"), "where is the memory test?", "Now: 10:00 PM")
    assert (chosen, away) == (route.NEW, False) and "no reading" in why


def test_jev_picks_a_route_and_its_meaning_is_the_reason(jev: Jev) -> None:
    jev.picked["route"] = route.COMMUNICATOR
    chosen, why, _, _ = route.picked(state.new_drive("demo", "coordinator", "s", "/tmp"), "where is the memory test?", "Now: 10:00 PM")
    assert chosen == route.COMMUNICATOR and why.startswith("Jev picked communicator: The communicator answers")


def test_the_request_keeps_the_newest_thread_rows_within_one_jev_request() -> None:
    rows = "\n".join(f"row {number} " + "x" * 400 for number in range(60))
    asked = route.request("status?", rows, {route.NEW: route.MEANINGS[route.NEW]})
    assert settled.size(asked, {}) <= settled.REQUEST_CHARS and "60" in asked["thread"] and "1" not in asked["thread"]


def test_content_tokens_drop_short_and_common_words() -> None:
    assert agents.tokens("Where is the memory staging test, and what about j6639?") == {"memory", "staging", "test", "j6639"}


def test_a_fresh_route_for_a_gone_handler_is_no_sticky_route() -> None:
    data = state.new_drive("demo", "coordinator", "s", "/tmp")
    data["routes"]["thread:1794380463.481959"] = {"handler": "h1", "at": state.stamp()}
    assert agents.sticky(data, ["thread:1794380463.481959"]) is None
