import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from drive import state, view
from drive.state import DriveError

CODE = str(Path(__file__).resolve().parent)
SITE = Path(__file__).resolve().parent / "site"
DRIVE_BIN = Path(__file__).resolve().parents[2] / "bin" / "drive"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
HOST = "127.0.0.1"
PORT_BASE = 41000
PORT_SPAN = 1000
START_SECONDS = 10


def port_of(name: str) -> int:
    """The drive's own port, the same on every start, so its link survives restarts."""
    return PORT_BASE + zlib.crc32(name.encode()) % PORT_SPAN


def url_of(port: int) -> str:
    return f"http://{HOST}:{port}/"


def changes(name: str) -> Iterator[None]:
    """Yield at once, then after each write kqueue reports to the drive's state directory or its events log."""
    queue = select.kqueue()
    watched: dict[Path, int] = {}
    while True:
        for path in (state.drive_dir(name), state.events_path(name)):
            if path not in watched and path.exists():
                watched[path] = os.open(path, os.O_RDONLY)
                flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND
                queue.control([select.kevent(watched[path], select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, flags)], 0)
        yield
        queue.control(None, 1)


class Board:
    """The drive's dashboard model, built once per change to its stored state and handed to every open page."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.changed = threading.Condition()
        self.version = 0
        self.body = b""
        self.events: list[dict] = []
        self.offset = 0

    def stamp(self) -> tuple[int, ...]:
        paths = (state.state_path(self.name), state.cards_path(self.name), state.events_path(self.name))
        return tuple(path.stat().st_mtime_ns if path.exists() else 0 for path in paths)

    def rebuild(self) -> None:
        moment = state.now()
        for event, end in state.read_events(self.name, self.offset):
            self.offset = end
            if event["kind"] in view.HISTORY_EVENTS:
                self.events.append(event)
        self.events = [event for event in self.events if moment - state.parse_time(event["at"]) <= view.HISTORY_FOR]
        found, source = view.stored_cards(self.name)
        model = view.board(state.load(self.name), found, source, self.events, moment) | {"code": CODE, "pid": os.getpid()}
        body = json.dumps(model).encode()
        with self.changed:
            self.body = body
            self.version += 1
            self.changed.notify_all()

    def follow(self) -> None:
        built = None
        for _ in changes(self.name):
            if (stamp := self.stamp()) != built:
                built = stamp
                self.rebuild()

    def after(self, version: int) -> tuple[int, bytes]:
        """The model once it is newer than version, waiting for the next change when it is not."""
        with self.changed:
            self.changed.wait_for(lambda: self.version > version)
            return self.version, self.body


def handler(name: str, port: int, board: Board) -> type[BaseHTTPRequestHandler]:
    hosts = {f"{HOST}:{port}", f"localhost:{port}"}

    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Host") not in hosts:
                self.send_error(403)
            elif self.path == "/api/view":
                self.reply(board.after(0)[1], "application/json")
            elif self.path == "/api/stream":
                self.stream()
            elif self.path in FILES:
                file, kind = FILES[self.path]
                self.reply((SITE / file).read_bytes(), kind)
            else:
                self.send_error(404)

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            version = 0
            while True:
                version, body = board.after(version)
                try:
                    self.wfile.write(b"data: " + body + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

        def reply(self, body: bytes, kind: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Page


def serve(name: str, port: int) -> None:
    """Serve the dashboard until following the drive's files fails, so a broken build ends the server rather than freezing its page."""
    board = Board(name)
    server = ThreadingHTTPServer((HOST, port), handler(name, port, board))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    board.follow()


def serving(name: str, port: int) -> dict | None:
    """The model the drive's port serves, or None when nothing answers there."""
    try:
        with urllib.request.urlopen(url_of(port) + "api/view", timeout=30) as answer:
            drawn = json.load(answer)
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None
    if drawn.get("drive") != name:
        raise DriveError(f"port {port} serves something else; pass --port")
    return drawn


def wait_until(ready: Callable[[], bool], log: Path) -> None:
    deadline = time.monotonic() + START_SECONDS
    while not ready():
        if time.monotonic() > deadline:
            raise DriveError(f"the dashboard did not start; see {log}")
        time.sleep(0.2)


def start(name: str, port: int) -> str:
    """The drive's dashboard link, starting its server in the background when none runs, or replacing one serving other code."""
    log = state.drive_dir(name) / "dashboard.log"
    drawn = serving(name, port)
    if drawn and drawn["code"] == CODE:
        return url_of(port)
    if drawn:
        os.kill(drawn["pid"], signal.SIGTERM)
        wait_until(lambda: serving(name, port) is None, log)
    with open(log, "a") as out:
        subprocess.Popen(
            [sys.executable, "-I", str(DRIVE_BIN), "dashboard", "--drive", name, "--port", str(port), "--serve"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )
    wait_until(lambda: serving(name, port) is not None, log)
    return url_of(port)
