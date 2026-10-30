import ast
import hashlib
import http.client
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_desk_runner import config, runner_module, shell


HTTPConnection = http.client.HTTPConnection


def fixture_call(port, path, method="GET"):
    connection = HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request(method, "/fixture/" + path)
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200, result
        return result
    finally:
        connection.close()


@pytest.fixture(params=[32, 10_000], ids=["history32", "history10000"])
def native_cci(request, tmp_path, monkeypatch):
    home = tmp_path / "native-bus"
    process = subprocess.Popen([os.environ["CCI_NATIVE_FIXTURE"], str(home), str(request.param)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        info = json.loads(process.stdout.readline())
        assert info["home"] == str(home) and info["population"] == request.param
        port = int(info["url"].rpartition(":")[2])
        monkeypatch.setenv("CCI_HOME", str(home))
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "landing-native-fixture")
        monkeypatch.setenv("CODEX_THREAD_ID", "")
        monkeypatch.setenv("CCI_AGENT_ID", "")
        monkeypatch.setenv("PATH", os.environ["CCI_NATIVE_BINARY_DIR"] + os.pathsep + os.environ["PATH"])

        def connect(host, requested_port, *, timeout):
            assert (host, requested_port) == ("127.0.0.1", 7377)
            return HTTPConnection(host, port, timeout=timeout)

        monkeypatch.setattr(runner_module.cci.http.client, "HTTPConnection", connect)
        yield SimpleNamespace(population=request.param, home=home, port=port, counts=lambda: fixture_call(port, "counts"), change=lambda operation: fixture_call(port, operation, "POST"))
    finally:
        process.stdin.close()
        code = process.wait(timeout=30)
        errors = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        assert code == 0, errors


def historical_io():
    path = Path(os.environ["CCI_LEGACY_DESK_RUNNER"])
    source = path.read_bytes()
    classes = {node.name: node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)}
    methods = []
    for owner, names in [("Shell", {"run", "run_once"}), ("Runner", {"cci_read"})]:
        methods.extend(node for node in classes[owner].body if isinstance(node, ast.FunctionDef) and node.name in names)
    assert len(methods) == 3
    namespace = vars(runner_module).copy()
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    legacy_shell = type("LegacyShell", (runner_module.Shell,), {name: namespace[name] for name in ("run", "run_once")})
    legacy_runner = type("LegacyRunner", (runner_module.Runner,), {"cci_read": namespace["cci_read"]})
    return legacy_shell, legacy_runner, hashlib.sha256(source).hexdigest()


def observed_commands(monkeypatch):
    costs = {"tail_processes": 0, "post_processes": 0, "temporary_feeds": 0}
    original_run = runner_module.subprocess.run
    original_feed = runner_module.tempfile.TemporaryFile

    def process(argv, *args, **kwargs):
        if argv[:2] == ["cci", "tail"]:
            costs["tail_processes"] += 1
        if argv[:2] == ["cci", "post"]:
            costs["post_processes"] += 1
        return original_run(argv, *args, **kwargs)

    def feed(*args, **kwargs):
        costs["temporary_feeds"] += 1
        return original_feed(*args, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", process)
    monkeypatch.setattr(runner_module.tempfile, "TemporaryFile", feed)
    return costs


def test_actual_landing_native_reads_keep_cache_and_fresh_mutable_holds(config, tmp_path, monkeypatch, native_cci):
    legacy_shell, legacy_runner, legacy_hash = historical_io()
    settings = runner_module.Config.load(config)
    legacy = runner_module.Landing(legacy_runner(legacy_shell(), settings, runner_module.actions.Store(tmp_path / "legacy-book")), settings.landing)
    current = runner_module.Landing(runner_module.Runner(runner_module.Shell(), settings, runner_module.actions.Store(tmp_path / "current-book")), settings.landing)
    costs = observed_commands(monkeypatch)
    try:
        legacy.cci_tail.remember()
        assert legacy.held({}) == ["29020"]
        assert legacy.held({}) == ["29020"]
        legacy.cci_tail.forget()
        assert legacy.held({}) == ["29020"]
        before = costs.copy()
        assert before == {"tail_processes": 2, "post_processes": 0, "temporary_feeds": 2}
        assert native_cci.counts()["native_requests"] == 0
        costs.update({key: 0 for key in costs})

        def held(expected):
            commands = costs.copy()
            assert current.held({}) == expected
            assert costs == commands

        current.cci_tail.remember()
        held(["29020"])
        held(["29020"])
        assert native_cci.counts()["native_requests"] == 1
        native_cci.change("append")
        held(["29020"])
        assert native_cci.counts()["native_requests"] == 1
        posted = current.runner.cci_post("note", "native cache refresh", "root", "native-ci", "native-ci-refresh")
        assert posted.code == 0, posted
        held(["29020", "29021"])
        native_cci.change("resolve")
        current.cci_tail.forget()
        held(["29021"])
        native_cci.change("expire")
        current.cci_tail.forget()
        held([])
        after = costs.copy()
        requests = native_cci.counts()["native_requests"]
        print(json.dumps({"history": native_cci.population, "provider_source": os.environ["CCI_NATIVE_PROVIDER_SOURCE"], "legacy_source_sha256": legacy_hash, "runner_sha256": hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest(), "native_client_sha256": hashlib.sha256(Path(runner_module.cci.__file__).read_bytes()).hexdigest(), "legacy_two_fresh_reads": before, "native_reads_and_one_post": after, "native_requests": requests, "freshness": ["cached hit", "external append waits for barrier", "own post invalidates", "resolve", "expire"]}))
        assert after == {"tail_processes": 0, "post_processes": 1, "temporary_feeds": 1}
        assert requests == 4
    finally:
        legacy.close()
        current.close()


def test_generic_shell_keeps_a_since_spelling_used_as_a_topic_value(native_cci):
    argv = ["cci", "tail", "--drive", "d1", "--topic", "--since", "--json", "--budget", "4000000"]
    result = runner_module.Shell().run(argv)
    print(json.dumps({"history": native_cci.population, "generic_cursor_mode_returncode": result.code, "native_requests": native_cci.counts()["native_requests"]}))
    assert result.code == 0, result
    assert native_cci.counts()["native_requests"] == 0
