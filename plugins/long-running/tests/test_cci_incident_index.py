import threading
import time
from concurrent.futures import ThreadPoolExecutor

from long_running import cci
import pytest
from test_cci_native_tail import native_endpoint
from test_desk_runner import config, landing_of, runner_module, shell


def use_native(shell, monkeypatch):
    monkeypatch.setattr(shell, "incident_holds", lambda home, drive, prs: runner_module.Shell.incident_holds(shell, home, drive, prs))


def test_incident_query_sends_exact_context_and_decimal_lexemes(native_endpoint):
    replies, requests, _ = native_endpoint
    record = {"seq": 41, "num": "#7@remote", "drive": "d1", "kind": "incident", "status": "open"}
    replies.append((200, {"029020": record}))
    assert cci.incident_holds("/expected/home", "d1", ["029020", "29020"], 5) == {"029020": record}
    assert requests == [("/v1/incident-holds", "application/json", {"home": "/expected/home", "drive": "d1", "prs": ["029020", "29020"]})]


@pytest.mark.parametrize(("status", "code"), [(400, "invalid_request"), (405, "method_not_allowed"), (409, "home_mismatch"), (413, "request_too_large"), (500, "query_failed"), (503, "scope_not_admitted"), (503, "projection_unavailable"), (503, "watch_authority_lost")])
def test_incident_query_preserves_typed_status_and_error(native_endpoint, status, code):
    replies, requests, _ = native_endpoint
    replies.append((status, {"code": code, "error": "fixture failure"}))
    with pytest.raises(cci.IncidentQueryFailed, match="fixture failure") as failed:
        cci.incident_holds("/expected/home", "d1", ["29020"], 5)
    assert (failed.value.status, failed.value.code) == (status, code)
    assert len(requests) == 1


def test_native_holding_queries_selected_records_fresh_without_python_body_reads(native_endpoint, shell, config, tmp_path, monkeypatch):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    landing = landing_of(shell, config, tmp_path)

    def unwanted(*args, **kwargs):
        raise AssertionError("native holding must not read the incident corpus or body files in Python")

    monkeypatch.setattr(runner_module, "full_text", unwanted)
    monkeypatch.setattr(cci, "records", unwanted)
    replies.extend([(200, {}), (200, {"29020": {"seq": 41}}), (200, {"29020": {"seq": 42}})])
    called = shell.called["cci incident-holds"]
    for seq in (41, 42):
        assert landing.holding(["29020"], {}, shell.now()) == {"29020": f"open INCIDENT #{seq}, whose lane squash-merges it directly, never through the queue"}
    assert [request[2]["prs"] for request in requests] == [[], ["29020"], ["29020"]]
    assert shell.called["cci incident-holds"] - called == 3
    assert not shell.in_progress


@pytest.mark.parametrize(("status", "code"), [(409, "home_mismatch"), (503, "scope_not_admitted")])
def test_only_initial_typed_nonselection_chooses_existing_operation_once(native_endpoint, shell, config, tmp_path, monkeypatch, status, code):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    runner = landing_of(shell, config, tmp_path).runner
    replies.append((status, {"code": code, "error": "unsupported context"}))
    assert runner.incident_holds(["29020"]) is None
    assert runner.incident_holds(["29021"]) is None
    assert runner.incident_native is False
    assert [request[2]["prs"] for request in requests] == [[]]


@pytest.mark.parametrize(("status", "code"), [(503, "projection_unavailable"), (503, "watch_authority_lost"), (500, "query_failed"), (500, "home_mismatch"), (409, "scope_not_admitted")])
def test_other_initial_errors_are_visible_then_next_ordinary_request_is_native(native_endpoint, shell, config, tmp_path, monkeypatch, status, code):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    runner = landing_of(shell, config, tmp_path).runner
    replies.extend([(status, {"code": code, "error": "initial failure"}), (200, {"29020": {"seq": 41}})])
    began = time.monotonic()
    with pytest.raises(runner_module.CciTailFailed, match="initial failure"):
        runner.incident_holds(["29020"])
    assert runner.incident_native is True and runner.read_failed_at >= began
    assert runner.incident_holds(["29020"]) == {"29020": {"seq": 41}}
    assert [request[2]["prs"] for request in requests] == [[], ["29020"]]


def test_initial_transport_timeout_does_not_repeat_admission_or_select_existing(native_endpoint, shell, config, tmp_path, monkeypatch):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    monkeypatch.setattr(runner_module, "QUICK_SECONDS", 0.03)
    runner = landing_of(shell, config, tmp_path).runner
    release = threading.Event()
    release.set()
    replies.extend([(None, release), (200, {"29020": {"seq": 41}})])
    with pytest.raises(runner_module.CciTailFailed, match="transport_failed"):
        runner.incident_holds(["29020"])
    assert runner.incident_native is True
    monkeypatch.setattr(runner_module, "QUICK_SECONDS", 5)
    assert runner.incident_holds(["29020"]) == {"29020": {"seq": 41}}
    assert [request[2]["prs"] for request in requests] == [[], ["29020"]]


@pytest.mark.parametrize("invalid", [[], {"29020": {"seq": 41}}])
def test_invalid_initial_success_body_is_visible_without_another_admission(native_endpoint, shell, config, tmp_path, monkeypatch, invalid):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    runner = landing_of(shell, config, tmp_path).runner
    replies.extend([(200, invalid), (200, {"29020": {"seq": 41}})])
    with pytest.raises(runner_module.CciTailFailed, match="invalid_response"):
        runner.incident_holds(["29020"])
    assert runner.incident_native is True
    assert runner.incident_holds(["29020"]) == {"29020": {"seq": 41}}
    assert [request[2]["prs"] for request in requests] == [[], ["29020"]]


def test_later_unsupported_error_stays_native_and_never_reselects(native_endpoint, shell, config, tmp_path, monkeypatch):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    runner = landing_of(shell, config, tmp_path).runner
    replies.extend([(200, {}), (200, {}), (503, {"code": "scope_not_admitted", "error": "relative body appeared"}), (200, {})])
    assert runner.incident_holds(["29020"]) == {}
    with pytest.raises(runner_module.CciTailFailed, match="relative body appeared"):
        runner.incident_holds(["29020"])
    assert runner.incident_native is True
    assert runner.incident_holds(["29020"]) == {}
    assert [request[2]["prs"] for request in requests] == [[], ["29020"], ["29020"], ["29020"]]


def test_admitted_context_home_is_fixed_for_the_runner(native_endpoint, shell, config, tmp_path, monkeypatch):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    monkeypatch.setenv("CCI_HOME", "first")
    expected = cci.native_home()
    runner = landing_of(shell, config, tmp_path).runner
    replies.extend([(200, {}), (200, {}), (200, {})])
    assert runner.incident_holds(["29020"]) == {}
    monkeypatch.setenv("CCI_HOME", "second")
    assert runner.incident_holds(["29020"]) == {}
    assert all(request[2]["home"] == expected for request in requests)


def test_concurrent_gate_workers_share_exactly_one_initial_admission(native_endpoint, shell, config, tmp_path, monkeypatch):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    runner = landing_of(shell, config, tmp_path).runner
    replies.extend([(200, {})] * 17)
    with ThreadPoolExecutor(max_workers=16) as workers:
        assert list(workers.map(lambda pr: runner.incident_holds([pr]), map(str, range(10000, 10016)))) == [{}] * 16
    assert len(requests) == 17
    assert [request[2]["prs"] for request in requests].count([]) == 1
    assert sorted(request[2]["prs"][0] for request in requests if request[2]["prs"]) == list(map(str, range(10000, 10016)))


def test_no_pr_operation_does_not_admit_or_query(native_endpoint, shell, config, tmp_path, monkeypatch):
    replies, requests, _ = native_endpoint
    use_native(shell, monkeypatch)
    landing = landing_of(shell, config, tmp_path)
    assert landing.holding([], {}, shell.now()) == {}
    assert landing.runner.incident_native is None
    assert not requests and not replies
