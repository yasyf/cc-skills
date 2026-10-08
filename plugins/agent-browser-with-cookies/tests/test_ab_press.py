"""Guards that `ab` presses keys into local Chrome without agent-browser's nativeVirtualKeyCode."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socketserver
import struct
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

AB = Path(__file__).resolve().parents[1] / "bin" / "ab"
TARGET_ID = "TARGET-1"
SESSION_ID = "SESSION-1"

COOKIESYNC = """#!/bin/bash
[ "$1" = requestor ] && echo test-requestor
"""

AGENT_BROWSER = """#!/bin/bash
echo "$*" >>"$AB_LOG"
[ "$1" = --session ] && shift 2
case "$*" in
  "get cdp-url") echo "$CDP_URL" ;;
  "tab --json") echo '{"success":true,"data":{"tabs":[
    {"active":false,"targetId":"OTHER"},{"active":true,"targetId":"%s"}]}}' ;;
esac
""" % TARGET_ID


class CdpHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        headers = {}
        self.rfile.readline()
        while (line := self.rfile.readline().decode().strip()) != "":
            name, _, value = line.partition(":")
            headers[name.lower()] = value.strip()
        accept = base64.b64encode(
            hashlib.sha1((headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        self.wfile.write(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        while (message := self.read_frame()) is not None:
            self.server.messages.append(message)
            result = {"sessionId": SESSION_ID} if message["method"] == "Target.attachToTarget" else {}
            self.write_frame(json.dumps({"id": message["id"], "result": result}).encode())

    def read_frame(self) -> dict | None:
        head = self.rfile.read(2)
        if len(head) < 2 or head[0] & 0x0F == 0x8:
            return None
        length = head[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self.rfile.read(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self.rfile.read(8))
        mask = self.rfile.read(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self.rfile.read(length)))
        return json.loads(payload)

    def write_frame(self, payload: bytes) -> None:
        size = len(payload)
        header = bytes([0x81, size]) if size < 126 else bytes([0x81, 126]) + struct.pack(">H", size)
        self.wfile.write(header + payload)


@pytest.fixture
def cdp() -> Iterator[socketserver.ThreadingTCPServer]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CdpHandler)
    server.daemon_threads = True
    server.messages = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def run_ab(tmp_path: Path, cdp: socketserver.ThreadingTCPServer, *argv: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("cookiesync", COOKIESYNC), ("agent-browser", AGENT_BROWSER)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "agent-browser.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AB_LOG": str(log),
        "CDP_URL": f"ws://127.0.0.1:{cdp.server_address[1]}/devtools/browser/test",
    }
    proc = subprocess.run([str(AB), *argv], capture_output=True, text=True, env=env, timeout=30)
    return proc, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("verb", ["press", "key"])
def test_press_dispatches_into_the_active_tab_without_a_native_key_code(tmp_path: Path, cdp, verb: str):
    proc, calls = run_ab(tmp_path, cdp, "--local", verb, "Escape")
    assert proc.returncode == 0, proc.stderr
    assert not any(c.endswith(f" {verb} Escape") for c in calls)
    attach, down, up, detach = cdp.messages
    assert attach["params"] == {"targetId": TARGET_ID, "flatten": True}
    assert [down["params"]["type"], up["params"]["type"]] == ["keyDown", "keyUp"]
    for event in (down, up):
        assert event["sessionId"] == SESSION_ID
        assert event["params"]["windowsVirtualKeyCode"] == 27
        assert "nativeVirtualKeyCode" not in event["params"]
    assert detach["method"] == "Target.detachFromTarget"


def test_command_chords_carry_modifiers_and_no_text(tmp_path: Path, cdp):
    proc, _ = run_ab(tmp_path, cdp, "--local", "press", "Control+a")
    assert proc.returncode == 0, proc.stderr
    down = cdp.messages[1]["params"]
    assert (down["key"], down["code"], down["modifiers"]) == ("a", "KeyA", 2)
    assert "text" not in down


def test_enter_carries_its_text_so_forms_submit(tmp_path: Path, cdp):
    proc, _ = run_ab(tmp_path, cdp, "--local", "press", "Enter")
    assert proc.returncode == 0, proc.stderr
    assert cdp.messages[1]["params"]["text"] == "\r"


def test_press_with_extra_arguments_refuses(tmp_path: Path, cdp):
    proc, calls = run_ab(tmp_path, cdp, "--local", "press", "Enter", "--json")
    assert proc.returncode == 2
    assert "agent-browser#2053" in proc.stderr
    assert calls == [] and cdp.messages == []


def test_other_commands_pass_through(tmp_path: Path, cdp):
    proc, calls = run_ab(tmp_path, cdp, "--local", "snapshot")
    assert proc.returncode == 0, proc.stderr
    assert calls[-1].endswith(" snapshot")
    assert cdp.messages == []
