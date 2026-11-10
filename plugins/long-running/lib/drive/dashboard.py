import json
import mimetypes
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple

from drive import github, state, view
from drive.state import DriveError

CODE = str(Path(__file__).resolve().parent)
SITE = Path(__file__).resolve().parent / "site"
DRIVE_BIN = Path(__file__).resolve().parents[2] / "bin" / "drive"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
LOOPBACK = "127.0.0.1"
LOCAL_CALLERS = {LOOPBACK, "::1", "::ffff:127.0.0.1"}
PORT_BASE = 41000
PORT_SPAN = 1000
START_SECONDS = 10
ANSWER_SECONDS = 60
PR_FIELDS = "title,body,url,state,isDraft,additions,deletions,changedFiles,headRefName,baseRefName,reviewDecision"
DIFF_LINES = 6000
PR_PATH = re.compile(r"/api/pr/([\w.-]+/[\w.-]+)/(\d+)")
NOT_OWNER = "only the drive's owner can do this, and this device is not signed in to the tailnet as him"
GONE = "this card no longer waits on that answer"


class Tailnet(NamedTuple):
    label: str
    ips: list[str]
    login: str


def port_of(name: str) -> int:
    """The drive's own port, the same on every start, so its link survives restarts."""
    return PORT_BASE + zlib.crc32(name.encode()) % PORT_SPAN


def url_of(port: int) -> str:
    return f"http://{LOOPBACK}:{port}/"


def tailnet() -> Tailnet | None:
    """This machine's bare MagicDNS label, its tailnet addresses and the login it is signed in as, or None when tailscale is not running here."""
    try:
        done = subprocess.run(
            ["tailscale", "status", "--self", "--peers=false", "--json"], capture_output=True, text=True, timeout=10, check=False
        )
    except FileNotFoundError:
        return None
    if done.returncode:
        return None
    status = json.loads(done.stdout)
    if status["BackendState"] != "Running":
        return None
    me = status["Self"]
    return Tailnet(me["DNSName"].split(".")[0], me["TailscaleIPs"], status["User"][str(me["UserID"])]["LoginName"])


def links_of(port: int, net: Tailnet | None) -> list[str]:
    """The tailnet link on the bare label first, since ts.net is HSTS-preloaded and an http link on the full name cannot load, then loopback."""
    return ([f"http://{net.label}:{port}/"] if net else []) + [url_of(port)]


def owner_calling(address: str, net: Tailnet | None) -> bool:
    if address in LOCAL_CALLERS:
        return True
    if not net:
        return False
    done = subprocess.run(["tailscale", "whois", "--json", address], capture_output=True, text=True, timeout=10, check=False)
    if done.returncode:
        return False
    who = json.loads(done.stdout)
    return not who["Node"].get("Tags") and who["UserProfile"]["LoginName"] == net.login


def answer_card(card: str, label: str, via: str) -> dict:
    done = subprocess.run(
        ["cc-slack", "card-answer", "--card", card, "--label", label, "--via", via, "--surface", "dashboard"],
        capture_output=True,
        text=True,
        timeout=ANSWER_SECONDS,
        check=False,
    )
    if done.returncode:
        raise DriveError(" ".join(done.stderr.split()) or f"cc-slack card-answer exited {done.returncode}")
    return json.loads(done.stdout)


def pr_read(key: str) -> dict:
    repo, number = key.split("#")
    read = json.loads(github.run(["gh", "pr", "view", number, "-R", repo, "--json", PR_FIELDS]))
    diff = github.run(["gh", "pr", "diff", number, "-R", repo])
    lines = diff.splitlines()
    return read | {"diff": "\n".join(lines[:DIFF_LINES]), "diff_lines": len(lines)}


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


class Server6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class Board:
    """The drive's dashboard model, built once per change to its stored state and handed to every open page."""

    def __init__(self, name: str, links: list[str]) -> None:
        self.name = name
        self.links = links
        self.changed = threading.Condition()
        self.version = 0
        self.body = b""
        self.events: list[dict] = []
        self.offset = 0
        self.answers: dict[str, list[str]] = {}
        self.prs: set[str] = set()
        self.pictures: set[str] = set()

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
        model = view.board(state.load(self.name), found, source, self.events, moment) | {"code": CODE, "pid": os.getpid(), "links": self.links}
        body = json.dumps(model).encode()
        answers = {item["answer"]: item["card"]["options"] for item in [*model["queue"], *model["review"]] if item.get("answer")}
        prs = {entry["key"] for entry in model["review"]}
        cards = [item["card"] for item in [*model["queue"], *model["review"]] if item.get("card")]
        pictures = {path for card in cards for path in [*(card.get("pictures") or []), *(path for row in card.get("prs") or [] for path in row.get("pictures") or [])]}
        with self.changed:
            self.body = body
            self.answers = answers
            self.prs = prs
            self.pictures = pictures
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


def handler(port: int, net: Tailnet | None, board: Board) -> type[BaseHTTPRequestHandler]:
    names = [LOOPBACK, "localhost"] + ([net.label] + [f"[{ip}]" if ":" in ip else ip for ip in net.ips] if net else [])
    hosts = {f"{host}:{port}" for host in names}

    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Host") not in hosts:
                self.send_error(403)
            elif self.path == "/api/view":
                self.reply(board.after(0)[1], "application/json")
            elif self.path == "/api/stream":
                self.stream()
            elif found := PR_PATH.fullmatch(self.path):
                self.pull_request(f"{found[1]}#{found[2]}")
            elif self.path.startswith("/api/picture?"):
                self.picture(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["path"][0])
            elif self.path in FILES:
                file, kind = FILES[self.path]
                self.reply((SITE / file).read_bytes(), kind)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            host = self.headers.get("Host")
            if host not in hosts or self.headers.get("Origin") != f"http://{host}":
                self.send_error(403)
            elif self.path != "/api/answer":
                self.send_error(404)
            elif not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            else:
                self.answer(json.loads(self.rfile.read(int(self.headers["Content-Length"]))), f"http://{host}/")

        def picture(self, path: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.send_error(403)
            elif path not in board.pictures or not Path(path).is_file():
                self.send_error(404)
            else:
                self.reply(Path(path).read_bytes(), mimetypes.guess_type(path)[0] or "application/octet-stream")

        def pull_request(self, key: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif key not in board.prs:
                self.answered(404, {"error": f"{key} is not an open PR of this drive"})
            else:
                try:
                    self.answered(200, pr_read(key))
                except github.GithubError as error:
                    self.answered(502, {"error": str(error)})

        def answer(self, asked: dict, via: str) -> None:
            if asked["label"] not in board.answers.get(asked["card"], []):
                self.answered(409, {"error": GONE})
                return
            try:
                said = answer_card(asked["card"], asked["label"], via)
            except (DriveError, subprocess.TimeoutExpired) as error:
                self.answered(502, {"error": str(error)})
                return
            view.refresh_cards(board.name)
            self.answered(200, said)

        def answered(self, status: int, said: dict) -> None:
            body = json.dumps(said).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
    """Serve the dashboard on loopback and this machine's own tailnet addresses, never a wildcard, until following the drive's files fails, so a broken build ends the server rather than freezing its page."""
    net = tailnet()
    board = Board(name, links_of(port, net))
    page = handler(port, net, board)
    for address in [LOOPBACK] + (net.ips if net else []):
        server = (Server6 if ":" in address else ThreadingHTTPServer)((address, port), page)
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


def start(name: str, port: int) -> list[str]:
    """The drive's dashboard links, tailnet first, starting its server in the background when none runs, or replacing one serving other code or other addresses."""
    log = state.drive_dir(name) / "dashboard.log"
    drawn = serving(name, port)
    if drawn and drawn["code"] == CODE and drawn["links"] == links_of(port, tailnet()):
        return drawn["links"]
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
    return serving(name, port)["links"]
