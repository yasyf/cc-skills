import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from native_cci_integration import HTTPConnection, fixture_call
from test_cci_incident_index import use_native
from test_desk_runner import config, landing_of, runner_module, shell


@pytest.fixture(params=[32, 10_000], ids=["history32", "history10000"])
def incident_index(request, tmp_path, monkeypatch):
    home = tmp_path / "indexed-incidents"
    process = subprocess.Popen([os.environ["CCI_INCIDENT_FIXTURE"], str(home), str(request.param)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        info = json.loads(process.stdout.readline())
        assert info["home"] == str(home) and info["population"] == request.param
        port = int(info["url"].rpartition(":")[2])
        monkeypatch.setenv("CCI_HOME", str(home))

        def connect(host, requested_port, *, timeout):
            assert (host, requested_port) == ("127.0.0.1", 7377)
            return HTTPConnection(host, port, timeout=timeout)

        monkeypatch.setattr(runner_module.cci.http.client, "HTTPConnection", connect)
        yield SimpleNamespace(population=request.param, info=info, counts=lambda: fixture_call(port, "counts"), change=lambda operation: fixture_call(port, operation, "POST"))
    finally:
        process.stdin.close()
        code = process.wait(timeout=30)
        errors = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        assert code == 0, errors


def test_actual_landing_native_incidents_follow_mutable_files_and_closures(incident_index, shell, config, tmp_path, monkeypatch):
    use_native(shell, monkeypatch)
    landing = landing_of(shell, config, tmp_path)
    selected = incident_index.info["selected"]
    prefix = ["29020", "29021", "29022", "29023"]

    def unwanted(*args, **kwargs):
        raise AssertionError("native Landing holding must not open Python incident bodies")

    def held(*prs, seq=selected):
        return {pr: f"open INCIDENT #{seq}, whose lane squash-merges it directly, never through the queue" for pr in prs}

    monkeypatch.setattr(runner_module, "full_text", unwanted)
    calls = []
    work = []

    def query(expected, *, changed=False):
        before = incident_index.counts()
        assert landing.holding(prefix, {}, shell.now()) == expected
        after = incident_index.counts()
        calls.append(after["native_requests"] - before["native_requests"])
        delta = {key: after["backend"][key] - before["backend"][key] for key in ("body_stats", "body_opens", "body_bytes", "route_stats", "missing_name_checks")}
        work.append(delta)
        if changed:
            assert delta["body_stats"] <= 4
            assert delta["body_opens"] <= 1
            assert delta["body_bytes"] <= 64
            assert delta["route_stats"] <= 64
            assert delta["missing_name_checks"] <= 4
        else:
            assert delta == {"body_stats": 0, "body_opens": 0, "body_bytes": 0, "route_stats": 0, "missing_name_checks": 0}

    try:
        query(held("29020", "29022"), changed=True)
        assert work[-1]["body_opens"] == work[-1]["body_bytes"] == 0
        query(held("29020", "29022"))
        query(held("29020", "29022"))
        for operation, prs in [("body-unicode-nonmatch", ["29022"]), ("body-match", ["29020", "29022"]), ("body-rename", ["29022", "29023"]), ("body-remove", ["29021", "29022"]), ("body-recreate", ["29020", "29022"]), ("body-invalid", ["29021", "29022"]), ("body-match", ["29020", "29022"])]:
            incident_index.change(operation)
            query(held(*prs), changed=True)
        newer = incident_index.change("append")["newer"]
        query(held("29020", seq=newer) | held("29022"))
        incident_index.change("resolve-newer")
        query(held("29020", "29022"))
        incident_index.change("expire-selected")
        query({}, changed=True)
        assert calls == [2] + [1] * (len(calls) - 1)
        incident_index.change("unsupported-relative")
        for _ in range(2):
            before = incident_index.counts()["native_requests"]
            with pytest.raises(runner_module.CciTailFailed):
                landing.holding(prefix, {}, shell.now())
            assert incident_index.counts()["native_requests"] == before + 1
            assert landing.runner.incident_native is True
        print(json.dumps({"population": incident_index.population, "provider_source": os.environ["CCI_INCIDENT_PROVIDER_SOURCE"], "runner_sha256": hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest(), "native_client_sha256": hashlib.sha256(Path(runner_module.cci.__file__).read_bytes()).hexdigest(), "native_requests_per_ordinary_call": calls, "native_body_work_per_ordinary_call": work, "controls": ["body-only and structured refs", "unchanged", "Unicode word boundary", "write", "rename", "remove", "recreate", "strict decode fallback", "latest open", "resolve", "expiry", "later unsupported scope remains visible without fallback"], "limits": "Actual Landing+provider handler body/stat counters; provider SQL/query-plan cost proof remains separate. Kernel events drained synchronously at each real request, with fixture mutations completed before request entry. No global atomic filesystem snapshot claim."}))
    finally:
        landing.close()
