import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from drive import state, view
from drive.state import DriveError

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


def handler(name: str, port: int) -> type[BaseHTTPRequestHandler]:
    hosts = {f"{HOST}:{port}", f"localhost:{port}"}

    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Host") not in hosts:
                self.send_error(403)
            elif self.path == "/api/view":
                self.reply(json.dumps(view.read(name, state.now())).encode(), "application/json")
            elif self.path in FILES:
                file, kind = FILES[self.path]
                self.reply((SITE / file).read_bytes(), kind)
            else:
                self.send_error(404)

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
    ThreadingHTTPServer((HOST, port), handler(name, port)).serve_forever()


def serving(name: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(url_of(port) + "api/view", timeout=30) as answer:
            drawn = json.load(answer)
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False
    if drawn.get("drive") != name:
        raise DriveError(f"port {port} serves something else; pass --port")
    return True


def start(name: str, port: int) -> str:
    """The drive's dashboard link, starting its server in the background when none runs."""
    if serving(name, port):
        return url_of(port)
    log = state.drive_dir(name) / "dashboard.log"
    with open(log, "a") as out:
        subprocess.Popen(
            [sys.executable, "-I", str(DRIVE_BIN), "dashboard", "--drive", name, "--port", str(port), "--serve"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )
    deadline = time.monotonic() + START_SECONDS
    while not serving(name, port):
        if time.monotonic() > deadline:
            raise DriveError(f"the dashboard did not start; see {log}")
        time.sleep(0.2)
    return url_of(port)
