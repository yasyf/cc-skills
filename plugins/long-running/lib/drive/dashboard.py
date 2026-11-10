import json
import mimetypes
import os
import re
import select
import signal
import socket
import ssl
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
CERT_SECONDS = 120
CERT_REFRESH_SECONDS = 24 * 60 * 60
PR_FIELDS = "title,body,url,state,isDraft,additions,deletions,changedFiles,headRefName,baseRefName,reviewDecision"
DIFF_LINES = 6000
THREAD_PATH = re.compile(r"/api/thread/(\d+\.\d+)")
THREAD_MESSAGES = "200"
PR_PATH = re.compile(r"/api/pr/([\w.-]+/[\w.-]+)/(\d+)")
NOT_OWNER = "only the drive's owner can do this, and this device is not signed in to the tailnet as him"
GONE = "this card no longer waits on that answer"
ELSEWHERE = "that is not a card or ask thread of this drive"


class Tailnet(NamedTuple):
    label: str
    name: str
    ips: list[str]
    login: str
    https: bool


def port_of(name: str) -> int:
    """The drive's own port, the same on every start, so its link survives restarts."""
    return PORT_BASE + zlib.crc32(name.encode()) % PORT_SPAN


def url_of(port: int) -> str:
    return f"http://{LOOPBACK}:{port}/"


def tailnet() -> Tailnet | None:
    """This machine's MagicDNS label and name, its tailnet addresses, the login it is signed in as and whether the tailnet issues it HTTPS certificates, or None when tailscale is not running here."""
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
    name = me["DNSName"].rstrip(".")
    return Tailnet(name.split(".")[0], name, me["TailscaleIPs"], status["User"][str(me["UserID"])]["LoginName"], name in (status.get("CertDomains") or []))


def links_of(port: int, net: Tailnet | None) -> list[str]:
    """The tailnet link first, then loopback: https on the full name when the tailnet issues certificates, so browsers there allow notifications, else http on the bare label, since ts.net is HSTS-preloaded and an http link on the full name cannot load."""
    tailnet_link = (f"https://{net.name}:{port}/" if net.https else f"http://{net.label}:{port}/") if net else None
    return [link for link in (tailnet_link, url_of(port)) if link]


def link(name: str) -> str:
    """The drive's dashboard link the status page ends with, on the tailnet when tailscale runs here."""
    return links_of(port_of(name), tailnet())[0]


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


def cc_slack(*args: str) -> dict:
    done = subprocess.run(["cc-slack", *args], capture_output=True, text=True, timeout=ANSWER_SECONDS, check=False)
    if done.returncode:
        raise DriveError(" ".join(done.stderr.split()) or f"cc-slack {args[0]} exited {done.returncode}")
    return json.loads(done.stdout)


def answer_card(card: str, label: str, via: str) -> dict:
    return cc_slack("card-answer", "--card", card, "--label", label, "--via", via, "--surface", "dashboard")


def relay(text: str, under: str) -> dict:
    """Post his words in his DM, marked as typed on the dashboard, under the card or ask thread named, or as a new message; the drive hears them as his DM."""
    return cc_slack("dm-relay", "--text", text, *(["--under", under] if under else []))


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


class Certificate:
    """This machine's Tailscale certificate, fetched again once a day so `tailscale cert` renews it before it expires."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.lock = threading.Lock()
        self.fetched = 0.0
        self.fetch()

    def fetch(self) -> None:
        folder = state.root() / "tls"
        folder.mkdir(parents=True, exist_ok=True)
        cert, key = folder / f"{self.name}.crt", folder / f"{self.name}.key"
        subprocess.run(["tailscale", "cert", "--cert-file", str(cert), "--key-file", str(key), self.name], capture_output=True, timeout=CERT_SECONDS, check=True)
        self.context.load_cert_chain(cert, key)
        self.fetched = time.monotonic()

    def wrap(self, request: socket.socket) -> ssl.SSLSocket:
        with self.lock:
            if time.monotonic() - self.fetched > CERT_REFRESH_SECONDS:
                try:
                    self.fetch()
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ssl.SSLError) as error:
                    print(f"renewing the certificate for {self.name} failed, so the current one stays: {error}", file=sys.stderr, flush=True)
                    self.fetched = time.monotonic()
        return self.context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)


class Server(ThreadingHTTPServer):
    """One address's server, speaking TLS with the certificate when given one; the handshake runs in the request's own thread."""

    def __init__(self, address: str, port: int, page: type[BaseHTTPRequestHandler], certificate: Certificate | None) -> None:
        self.address_family = socket.AF_INET6 if ":" in address else socket.AF_INET
        self.certificate = certificate
        super().__init__((address, port), page)

    def get_request(self) -> tuple[socket.socket, object]:
        request, client = super().get_request()
        return (self.certificate.wrap(request) if self.certificate else request), client


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
        self.unders: set[str] = set()
        self.prs: set[str] = set()
        self.pictures: set[str] = set()
        self.threads: set[str] = set()
        self.channel = ""

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
            self.unders = set(answers) | {ask["url"] for ask in model["asks"] if ask["url"]}
            self.prs = prs
            self.pictures = pictures
            self.threads = {ask["thread"] for ask in model["asks"] if ask["thread"]}
            self.channel = model["channel"]
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
    names = [LOOPBACK, "localhost"] + ([net.label, *([net.name] if net.https else []), *(f"[{ip}]" if ":" in ip else ip for ip in net.ips)] if net else [])
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
            elif found := THREAD_PATH.fullmatch(self.path):
                self.thread(found[1])
            elif self.path.startswith("/api/picture?"):
                self.picture(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["path"][0])
            elif self.path in FILES:
                file, kind = FILES[self.path]
                self.reply((SITE / file).read_bytes(), kind)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            host = self.headers.get("Host")
            page = f"{'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'}://{host}"
            if host not in hosts or self.headers.get("Origin") != page:
                self.send_error(403)
            elif self.path not in ("/api/answer", "/api/say"):
                self.send_error(404)
            elif not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif self.path == "/api/say":
                self.say(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            else:
                self.answer(json.loads(self.rfile.read(int(self.headers["Content-Length"]))), f"{page}/")

        def thread(self, ts: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif ts not in board.threads:
                self.answered(404, {"error": f"{ts} is not the thread of an ask of this drive"})
            else:
                try:
                    self.answered(200, cc_slack("thread", "--channel", board.channel, "--thread", ts, "--limit", THREAD_MESSAGES))
                except (DriveError, subprocess.TimeoutExpired) as error:
                    self.answered(502, {"error": str(error)})

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

        def say(self, typed: dict) -> None:
            text, under = typed["text"].strip(), typed["under"]
            if not text:
                self.answered(400, {"error": "there are no words to send"})
                return
            if under and under not in board.unders:
                self.answered(409, {"error": ELSEWHERE})
                return
            try:
                self.answered(200, relay(text, under))
            except (DriveError, subprocess.TimeoutExpired) as error:
                self.answered(502, {"error": str(error)})

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
    """Serve the dashboard on loopback and this machine's own tailnet addresses, never a wildcard, the tailnet ones over HTTPS when the tailnet issues certificates, until following the drive's files fails, so a broken build ends the server rather than freezing its page."""
    net = tailnet()
    board = Board(name, links_of(port, net))
    page = handler(port, net, board)
    certificate = Certificate(net.name) if net and net.https else None
    for address in [LOOPBACK] + (net.ips if net else []):
        server = Server(address, port, page, certificate if address != LOOPBACK else None)
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
