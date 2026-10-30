import base64
import http.client
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cci
import pytest


@pytest.fixture(params=[False, True], ids=["content-length", "chunked"])
def native_endpoint(monkeypatch, request):
    replies, requests, connections = [], [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            requests.append((self.path, self.headers["Content-Type"], json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            status, payload = replies.pop(0)
            if status is None:
                payload.wait()
                return
            if status == 0:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload) + 10))
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True
                return
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if request.param:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if request.param:
                for start in range(0, len(body), 7):
                    chunk = body[start:start + 7]
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            else:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    original = http.client.HTTPConnection

    def connect(host, port, *, timeout):
        assert (host, port) == ("127.0.0.1", 7377)
        connection = original(host, server.server_port, timeout=timeout)
        connections.append(connection)
        return connection

    monkeypatch.setattr(cci.http.client, "HTTPConnection", connect)
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.invalid:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid.invalid:1")
    try:
        yield replies, requests, connections
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        assert all(connection.sock is None for connection in connections)
        assert not replies


def envelope(code=0, stdout=b"", stderr=b""):
    return {"returncode": code, "stdout_b64": base64.b64encode(stdout).decode(), "stderr_b64": base64.b64encode(stderr).decode()}


@pytest.mark.parametrize("code", [0, 1])
def test_native_tail_preserves_exact_subprocess_text_decoding(native_endpoint, code):
    replies, requests, connections = native_endpoint
    stdout, stderr = b'{"text":"caf\xc3\xa9"}\r\npartial\xff\r', b'cci: bad\xfe\r\nnext\r'
    reference = subprocess.run([sys.executable, "-c", f"import os;os.write(1,{stdout!r});os.write(2,{stderr!r});raise SystemExit({code})"], capture_output=True, text=True, errors="replace")
    replies.append((200, envelope(code, stdout, stderr)))
    argv = ["cci", "tail", "--drive", "d1", "--since", "0", "--json"]
    assert cci.native_tail(argv, 5) == (reference.returncode, reference.stdout, reference.stderr)
    assert requests[0][:2] == ("/v1/tail", "application/json")
    assert requests[0][2]["argv"] == argv
    assert len(connections) == 1 and connections[0].sock is None


@pytest.mark.parametrize("claude,codex,agent,session", [("claude", "codex", "child", "claude"), ("", "codex", "", "codex"), ("", "", "orphan", "")])
@pytest.mark.parametrize("home_kind", ["default", "absolute", "relative"])
def test_native_tail_binds_effective_child_identity_and_home_without_mutating_env(native_endpoint, monkeypatch, tmp_path, claude, codex, agent, session, home_kind):
    replies, requests, _ = native_endpoint
    monkeypatch.setenv("CCI_HOME", "/parent/store")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    child = {"HOME": str(tmp_path), "CCI_HOME": "", "CLAUDE_CODE_SESSION_ID": claude, "CODEX_THREAD_ID": codex, "CCI_AGENT_ID": agent}
    if home_kind == "absolute":
        child["CCI_HOME"] = str(tmp_path / "store" / ".." / "kept")
        expected_home = str(tmp_path / "kept")
    elif home_kind == "relative":
        child["CCI_HOME"] = "../store/./kept"
        expected_home = str(tmp_path / "store" / "kept")
    else:
        expected_home = str(tmp_path / ".cc-inbox")
    before = dict(os.environ)
    argv = ["cci", "tail", "--drive=d1", "--since=0", "--kind=go", "--kind", "hold", "--to-kind=state", "--lane", "root", "--topic=réview", "--json"]
    replies.append((200, envelope()))
    assert cci.native_tail(argv, 5, child, str(cwd)) == (0, "", "")
    assert requests == [("/v1/tail", "application/json", {"argv": argv, "session": session, "agent": agent, "home": expected_home})]
    assert dict(os.environ) == before


@pytest.mark.parametrize("status,error", [(400, "explicit drive and since required"), (409, "native tail home mismatch"), (413, "native tail request too large")])
def test_native_tail_protocol_errors_are_visible_and_do_not_fall_back(native_endpoint, status, error):
    replies, requests, connections = native_endpoint
    replies.append((status, {"error": error}))
    assert cci.native_tail(["cci", "tail", "--drive", "d1", "--since", "0"], 5) == (1, "", f"cci: {error}\n")
    assert len(requests) == len(connections) == 1


@pytest.mark.parametrize("failure", [ConnectionRefusedError("refused"), http.client.RemoteDisconnected("Remote end closed")])
def test_native_tail_transport_errors_close_the_connection(monkeypatch, failure):
    closed = []

    class Connection:
        def request(self, *args):
            raise failure

        def close(self):
            closed.append(True)

    monkeypatch.setattr(cci.http.client, "HTTPConnection", lambda *args, **kwargs: Connection())
    code, out, err = cci.native_tail(["cci", "tail", "--drive", "d1", "--since", "0"], 5)
    assert code == 1 and not out and str(failure) in err
    assert closed == [True]


def test_native_tail_timeout_keeps_the_shell_timeout_classification(monkeypatch):
    closed = []

    class Connection:
        def request(self, *args):
            raise TimeoutError("native query timed out")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(cci.http.client, "HTTPConnection", lambda *args, **kwargs: Connection())
    with pytest.raises(TimeoutError, match="native query timed out"):
        cci.native_tail(["cci", "tail", "--drive", "d1", "--since", "0"], 5)
    assert closed == [True]



def test_a_blocked_native_header_uses_the_existing_shell_timeout_and_retry(native_endpoint, monkeypatch):
    from test_desk_runner import runner_module

    replies, _, connections = native_endpoint
    release = threading.Event()
    replies.extend([(None, release), (None, release)])
    monkeypatch.setattr(runner_module, "QUICK_SECONDS", 1)
    try:
        result = runner_module.Shell().cci_query(["cci", "tail", "--drive", "d1", "--since", "0", "--json"])
        assert result.code == runner_module.TIMED_OUT
        assert "timed out" in result.err
        assert len(connections) == 2
    finally:
        release.set()


def test_progressing_native_body_reads_do_not_extend_the_read_budget(monkeypatch):
    clock, closed, timeouts, reads = [0.0], [], [], []

    class Wire:
        def settimeout(self, value):
            timeouts.append(value)

    class Response:
        def isclosed(self):
            return False

        def read1(self, size):
            reads.append(size)
            clock[0] += 0.6
            return b"x"

    class Connection:
        sock = Wire()

        def request(self, *args):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(cci, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cci.http.client, "HTTPConnection", lambda *args, **kwargs: Connection())
    with pytest.raises(TimeoutError, match="read budget expired"):
        cci.native_tail(["cci", "tail", "--drive", "d1", "--since", "0"], 1)
    assert len(reads) == 2 and timeouts[-1] == pytest.approx(0.4)
    assert closed == [True]



def test_a_truncated_native_content_length_is_a_transport_failure(native_endpoint):
    replies, _, _ = native_endpoint
    replies.append((0, b'{"returncode":0'))
    code, out, err = cci.native_tail(["cci", "tail", "--drive", "d1", "--since", "0"], 5)
    assert code == 1 and not out and "IncompleteRead" in err
