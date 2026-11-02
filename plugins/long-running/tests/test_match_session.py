import json
import subprocess
import sys

import pytest

from long_running import match_session


@pytest.fixture
def children(monkeypatch):
    opened = []
    popen = subprocess.Popen

    def tracked(*args, **kwargs):
        child = popen(*args, **kwargs)
        opened.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", tracked)
    yield opened
    assert all(child.poll() is not None for child in opened)


def invoke(tmp_path, script, prepare=lambda: {"type": "query", "query": "hello", "exclude": []}, checkpoint=lambda: None, timeout=10):
    return match_session.run([sys.executable, "-I", "-B", "-c", script], prepare, cwd=tmp_path, env={"SCOPE_MARKER": "request-owned"}, checkpoint=checkpoint, timeout=timeout)


READY = 'print(\'{"type":"ready","protocol":1}\', flush=True)\n'
RESULT = 'print(json.dumps({"type":"result","answers":[]}), flush=True)\n'


def test_session_keeps_ready_query_result_order_and_request_context(tmp_path, children):
    marker = tmp_path / "held"
    script = 'import json,os,sys\nfrom pathlib import Path\nPath("held").write_text("ready")\n' + READY + 'q=json.loads(sys.stdin.readline())\nassert Path("held").read_text()=="query"\nprint(json.dumps({"type":"result","answers":[{"id":"a"*40,"title":os.environ["SCOPE_MARKER"],"body":q["query"]}]}),flush=True)\n'

    def prepare():
        assert marker.read_text() == "ready"
        marker.write_text("query")
        return {"type": "query", "query": "éaaaaaaaé 😀", "exclude": ["literal-long-id"]}

    assert invoke(tmp_path, script, prepare) == [{"id": "a" * 40, "title": "request-owned", "body": "éaaaaaaaé 😀"}]
    assert children[0].returncode == 0


def test_session_drains_large_stderr_and_both_pipe_directions(tmp_path, children):
    query = "界" * 100000
    script = 'import json,sys\nsys.stderr.write("x"*262144)\nsys.stderr.flush()\n' + READY + 'q=json.loads(sys.stdin.readline())\nprint(json.dumps({"type":"result","answers":[{"body":q["query"]}]}),flush=True)\n'
    assert invoke(tmp_path, script, lambda: {"type": "query", "query": query, "exclude": []}) == [{"body": query}]
    assert children[0].returncode == 0


def test_prepare_error_closes_input_and_reaps_natural_eof(tmp_path, children):
    error = ValueError("register failed")
    script = 'import sys\nfrom pathlib import Path\n' + READY + 'assert sys.stdin.read()==""\nPath("closed").write_text("closed")\n'

    def prepare():
        raise error

    with pytest.raises(ValueError) as caught:
        invoke(tmp_path, script, prepare)
    assert caught.value is error
    assert (tmp_path / "closed").read_text() == "closed"
    assert children[0].returncode == 0


def test_invalid_utf8_query_keeps_strict_encoder_and_natural_eof(tmp_path, children):
    script = 'import sys\nfrom pathlib import Path\n' + READY + 'assert sys.stdin.read()==""\nPath("closed").write_text("closed")\n'
    with pytest.raises(UnicodeEncodeError):
        invoke(tmp_path, script, lambda: {"type": "query", "query": "\ud800", "exclude": []})
    assert (tmp_path / "closed").read_text() == "closed"
    assert children[0].returncode == 0


def test_source_error_precedes_register_and_preserves_process_error(tmp_path, children):
    prepared = []
    with pytest.raises(subprocess.CalledProcessError) as caught:
        invoke(tmp_path, 'import sys\nsys.stderr.write("source failure")\nsys.exit(7)', lambda: prepared.append(True))
    assert prepared == []
    assert caught.value.returncode == 7
    assert caught.value.stderr == "source failure"
    assert children[0].returncode == 7


def test_checkpoint_error_identity_and_child_closure(tmp_path, children):
    error = RuntimeError("request abandoned")
    checked = []

    def checkpoint():
        checked.append(True)
        if len(checked) > 1:
            raise error

    with pytest.raises(RuntimeError) as caught:
        invoke(tmp_path, 'import sys\n' + READY + 'sys.stdin.read()', checkpoint=checkpoint)
    assert caught.value is error
    assert children[0].returncode is not None


def test_abandoned_request_starts_no_child(tmp_path, children):
    error = RuntimeError("already abandoned")

    def checkpoint():
        raise error

    with pytest.raises(RuntimeError) as caught:
        invoke(tmp_path, "pass", checkpoint=checkpoint)
    assert caught.value is error and children == []


def test_existing_deadline_expires_after_ready_without_restarting(tmp_path, children, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(match_session.time, "monotonic", lambda: clock[0])

    def prepare():
        clock[0] = 11.0
        return {"type": "query", "query": "hello", "exclude": []}

    with pytest.raises(subprocess.TimeoutExpired):
        invoke(tmp_path, 'import sys\n' + READY + 'sys.stdin.read()', prepare)
    assert len(children) == 1 and children[0].returncode is not None


@pytest.mark.parametrize("ready", [{"type": "ready", "protocol": 2}, {"type": "result", "answers": []}])
def test_unknown_ready_is_visible_and_closes_owned_child(tmp_path, children, ready):
    script = f'import sys\nprint({json.dumps(json.dumps(ready))},flush=True)\nsys.stdin.read()'
    with pytest.raises(ValueError, match="unexpected match-answers ready frame"):
        invoke(tmp_path, script)
    assert len(children) == 1


def test_eof_before_ready_is_visible(tmp_path, children):
    with pytest.raises(EOFError, match="ended before its next frame"):
        invoke(tmp_path, "pass")
    assert children[0].returncode == 0


def test_result_does_not_hide_nonzero_exit(tmp_path, children):
    script = 'import json,sys\n' + READY + 'sys.stdin.readline()\n' + RESULT + 'sys.stderr.write("pick close failed")\nsys.exit(4)'
    with pytest.raises(subprocess.CalledProcessError) as caught:
        invoke(tmp_path, script)
    assert caught.value.returncode == 4 and caught.value.stderr == "pick close failed"


def test_trailing_protocol_output_is_visible(tmp_path, children):
    script = 'import json,sys\n' + READY + 'sys.stdin.readline()\n' + RESULT + 'print("extra",flush=True)'
    with pytest.raises(ValueError, match="trailing match-answers output"):
        invoke(tmp_path, script)
    assert children[0].returncode == 0


def test_existing_install_window_retries_only_process_creation(tmp_path, children, monkeypatch):
    popen = subprocess.Popen
    attempts = []
    prepared = []

    def relinking(*args, **kwargs):
        attempts.append(args[0])
        if len(attempts) == 1:
            raise FileNotFoundError(2, "relinking", args[0][0])
        return popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", relinking)
    monkeypatch.setattr(match_session.cci, "INSTALL_RETRY_SECONDS", 0)
    script = 'import json,sys\n' + READY + 'sys.stdin.readline()\n' + RESULT

    def prepare():
        prepared.append(True)
        return {"type": "query", "query": "hello", "exclude": []}

    assert invoke(tmp_path, script, prepare) == []
    assert len(attempts) == 2 and len(children) == 1 and prepared == [True]


def test_prepare_missing_file_is_not_relaunched(tmp_path, children):
    error = FileNotFoundError(2, "register failed", sys.executable)
    script = 'import sys\n' + READY + 'assert sys.stdin.read()==""'

    def prepare():
        raise error

    with pytest.raises(FileNotFoundError) as caught:
        invoke(tmp_path, script, prepare)
    assert caught.value is error
    assert len(children) == 1 and children[0].returncode == 0


def test_install_window_keeps_request_cancellation(tmp_path, children, monkeypatch):
    error = RuntimeError("abandoned while relinking")
    checked, attempts = [], []

    def checkpoint():
        checked.append(True)
        if len(checked) == 2:
            raise error

    def relinking(*args, **kwargs):
        attempts.append(args[0])
        raise FileNotFoundError(2, "relinking", args[0][0])

    monkeypatch.setattr(subprocess, "Popen", relinking)
    monkeypatch.setattr(match_session.cci, "INSTALL_RETRY_SECONDS", 0)
    with pytest.raises(RuntimeError) as caught:
        invoke(tmp_path, "pass", checkpoint=checkpoint)
    assert caught.value is error and children == [] and len(attempts) == 1


def test_pipe_setup_failure_reaps_its_created_child(tmp_path, children, monkeypatch):
    error = OSError("private pipe setup")

    def fail(fd, blocking):
        raise error

    monkeypatch.setattr(match_session.os, "set_blocking", fail)
    with pytest.raises(OSError) as caught:
        invoke(tmp_path, "import sys; sys.stdin.read()")
    assert caught.value is error
    assert children[0].returncode is not None
