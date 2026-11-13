import functools
import http.client
import json
import mimetypes
import os
import plistlib
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

from drive import (
    artifacts,
    card,
    github,
    install,
    picture,
    review,
    slack,
    stage,
    state,
    view,
)
from drive.card import body_of, sections
from drive.state import DriveError

CODE = str(Path(__file__).resolve().parent)
SITE = Path(__file__).resolve().parent / "site"
DRIVE_BIN = Path(__file__).resolve().parents[2] / "bin" / "drive"
MANIFEST = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
AGENT_ENV = ("PATH", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME", "XDG_BIN_HOME", "CC_SLACK_CONFIG", "CHROME")
RESTART_SECONDS = 10
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/visuals.js": ("visuals.js", "text/javascript; charset=utf-8"),
}
VENDOR = SITE / "vendor"
VENDOR_KINDS = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
LOOPBACK = "127.0.0.1"
LOCAL_CALLERS = {LOOPBACK, "::1", "::ffff:127.0.0.1"}
PORT_BASE = 41000
PORT_SPAN = 1000
START_SECONDS = 10
HANDOVER_POLL_SECONDS = 1
ANSWER_SECONDS = 60
CERT_SECONDS = 120
CERT_REFRESH_SECONDS = 24 * 60 * 60
THREAD_PATH = re.compile(r"/api/thread/(\d+\.\d+)")
THREAD_MESSAGES = "200"
PR_PATH = re.compile(r"/api/pr/([\w.-]+/[\w.-]+)/(\d+)")
REVIEW_PATH = re.compile(r"/api/review/([\w.-]+/[\w.-]+)/(\d+)")
FILE_PATH = re.compile(r"/api/file/(j[\w-]+)/(\w+)")
REPO_FILE = re.compile(r"[\w.@-]+(?:/[\w.@-]+)+")
NOT_OWNER = "only the drive's owner can do this, and this device is not signed in to the tailnet as him"
GONE = "this card no longer waits on that answer"
HELD = "an answer to this card is already being sent"
UNDO_SECONDS = 10
ELSEWHERE = "that is not a card thread of this drive"
REDRAWN = "the card was drawn again while your answer was held, so nothing was sent; read it again and answer it as it stands"
TLS_HANDSHAKE = b"\x16"


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
    done = subprocess.run([slack.launcher(), *args], capture_output=True, text=True, timeout=ANSWER_SECONDS, check=False)
    if done.returncode:
        raise DriveError(" ".join(done.stderr.split()) or f"cc-slack {args[0]} exited {done.returncode}")
    return json.loads(done.stdout)


def revision(card: dict) -> tuple[str, list[str | None]]:
    """What an answer to a card answers: its question and the head of each PR it shows."""
    return card["title"], [row.get("head") for row in card.get("prs") or []]


def answer_card(card: str, label: str, via: str) -> dict:
    return cc_slack("card-answer", "--card", card, "--label", label, "--via", via, "--surface", "dashboard")


def relay(text: str, under: str) -> dict:
    """Post his words in his DM, marked as typed on the dashboard, under the card or ask thread named, or as a new message; the drive hears them as his DM."""
    return cc_slack("dm-relay", "--text", text, *(["--under", under] if under else []))


def pr_read(name: str, key: str) -> dict | None:
    """The PR as its Slack card reads it, with its Show me section cut by the parser that card uses and the row his card would draw for it, its stage as his card on it draws; None for a PR GitHub refuses that was never read."""
    data = state.load(name)
    entry = data["prs"][key]
    if not (pr := card.read(key, entry)):
        return None
    now = stage.on_card(data, key, view.live_cards(name))
    return pr | {"show_me": sections(body_of(pr)).get("show me", ""), "row": card.render(pr, entry | {"now": now})["prs"][0]}


@functools.cache
def home_repo(checkout: str) -> str | None:
    try:
        return github.checkout_repo(checkout)
    except github.GithubError:
        return None


def card_pictures(card: dict) -> set[str]:
    """Each picture a card draws: those its body shows by path, its own and those of each PR it carries."""
    drawn = {match[1] for match in picture.IMAGE.finditer(card.get("body") or "") if match[1] and Path(match[1]).is_absolute()}
    return drawn | {*(card.get("pictures") or []), *(path for row in card.get("prs") or [] for path in row.get("pictures") or [])}


def changes(name: str) -> Iterator[None]:
    """Yield at once, then after each write kqueue reports to the drive's state directory, its events log or Claude Code's plugins folder, where each install replaces installed_plugins.json, so an install wakes the dashboard as a change to the drive does."""
    queue = select.kqueue()
    watched: dict[Path, int] = {}
    while True:
        for path in (state.drive_dir(name), state.events_path(name), install.config_dir() / "plugins"):
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


class Upgrade(BaseHTTPRequestHandler):
    """Sends a plain HTTP request on a TLS port to the same path over HTTPS, so a link from before the tailnet issued certificates still opens."""

    def do_GET(self) -> None:
        self.send_response(301)
        self.send_header("Location", f"https://{self.server.certificate.name}:{self.server.server_address[1]}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, format: str, *args: object) -> None:
        pass


class Server(ThreadingHTTPServer):
    """One listening socket's server, speaking TLS with the certificate when given one and redirecting plain HTTP there to HTTPS; the first byte is read and the handshake runs in the request's own thread."""

    def __init__(self, listening: socket.socket, page: type[BaseHTTPRequestHandler], certificate: Certificate | None) -> None:
        self.address_family = listening.family
        self.certificate = certificate
        super().__init__(listening.getsockname(), page, bind_and_activate=False)
        self.socket.close()
        self.socket = listening

    def finish_request(self, request: socket.socket, client_address: tuple) -> None:
        if not self.certificate:
            super().finish_request(request, client_address)
        elif request.recv(1, socket.MSG_PEEK) != TLS_HANDSHAKE:
            Upgrade(request, client_address, self)
        else:
            with self.certificate.wrap(request) as secured:
                super().finish_request(secured, client_address)


class Board:
    """The drive's dashboard model, built once per change to its stored state and handed to every open page."""

    def __init__(self, name: str, links: list[str]) -> None:
        self.name = name
        self.links = links
        self.changed = threading.Condition()
        self.version = 0
        self.body = b""
        self.model: dict = {}
        self.events: list[dict] = []
        self.offset = 0
        self.answers: dict[str, list[str]] = {}
        self.revisions: dict[str, tuple[str, list[str | None]]] = {}
        self.heads: dict[str, str] = {}
        self.pictures: set[str] = set()
        self.threads: set[str] = set()
        self.reports: set[str] = set()
        self.repo: str | None = None
        self.channel = ""
        self.holding = threading.Lock()
        self.held: dict[str, threading.Event] = {}
        self.assistant = review.Assistant(name)

    def stamp(self) -> tuple[int, ...]:
        paths = (state.state_path(self.name), state.cards_path(self.name), state.events_path(self.name))
        return tuple(path.stat().st_mtime_ns if path.exists() else 0 for path in paths)

    def rebuild(self) -> None:
        for event, end in state.read_events(self.name, self.offset):
            self.offset = end
            if event["kind"] in view.HISTORY_EVENTS:
                self.events.append(event)
        found, source = view.stored_cards(self.name)
        reports = {path.name.removesuffix(".replies.jsonl") for path in (state.drive_dir(self.name) / "jobs").glob("*.replies.jsonl")}
        drive = state.load(self.name)
        repo = home_repo(drive["roles"]["coordinator"]["cwd"])
        model = view.board(drive, found, source, self.events, state.now(), reports) | {"code": CODE, "pid": os.getpid(), "links": self.links, "undo_seconds": UNDO_SECONDS, "repo": repo}
        self.reports = reports
        self.show(model)
        self.assistant.warm((entry["key"], entry["head"]) for entry in model["review"] if entry["group"] == "you")

    def show(self, model: dict) -> None:
        """Hand every open page the summary of model, keeping model whole for the screens that read their own part of it."""
        asked = [item for item in [*model["queue"], *model["review"]] if item.get("answer")]
        cards = [item["card"] for item in [*model["queue"], *model["review"]] if item.get("card")]
        pictures = {path for card in cards for path in card_pictures(card)}
        pictures |= {visual["picture"] for entry in model["history"] for visual in entry.get("visuals") or [] if "picture" in visual}
        with self.changed:
            self.version += 1
            self.body = json.dumps(view.summary(model) | {"version": self.version}).encode()
            self.model = model
            self.answers = {item["answer"]: item["card"]["options"] for item in asked}
            self.revisions = {item["answer"]: revision(item["card"]) for item in asked}
            self.heads = {entry["key"]: entry["head"] for entry in model["review"]}
            self.pictures = pictures
            self.threads = {item["thread"] for item in [*model["queue"], *model["review"]] if item.get("thread")}
            self.repo = model["repo"]
            self.channel = model["channel"]
            self.changed.notify_all()

    def follow(self) -> Path:
        """Rebuild on each change to the drive's stored state, holding this install's lock while it does so a newer install's first save waits for the rebuild, until a newer long-running is installed; then return that install's drive, still holding the lock, so this code never reads a drive it saved."""
        built = None
        for _ in changes(self.name):
            state.hold_install()
            if newer := install.upgraded():
                return newer
            if (stamp := self.stamp()) != built:
                built = stamp
                self.rebuild()
            state.let_go_install()

    def after(self, version: int) -> tuple[int, bytes]:
        """The summary once it is newer than version, waiting for the next change when it is not."""
        with self.changed:
            self.changed.wait_for(lambda: self.version > version)
            return self.version, self.body


def handler(port: int, net: Tailnet | None, board: Board) -> type[BaseHTTPRequestHandler]:
    names = [LOOPBACK, "localhost"] + ([net.label, *([net.name] if net.https else []), *(f"[{ip}]" if ":" in ip else ip for ip in net.ips)] if net else [])
    hosts = {f"{host}:{port}" for host in names}

    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            where = urllib.parse.urlsplit(self.path)
            asked = dict(urllib.parse.parse_qsl(where.query))
            if self.headers.get("Host") not in hosts:
                self.send_error(403)
            elif self.path == "/api/view":
                self.reply(board.after(0)[1], "application/json")
            elif self.path == "/api/stream":
                self.stream()
            elif where.path == "/api/card":
                self.card(asked["answer"])
            elif where.path == "/api/task":
                self.task(asked["id"])
            elif where.path == "/api/story":
                self.answered(200, {"moments": view.story(board.model, set(asked["ids"].split(",")) if "ids" in asked else None)})
            elif where.path == "/api/history":
                self.answered(200, view.history_page(board.model, asked.get("day"), asked.get("show", "all"), int(asked.get("page", 1))))
            elif self.path == "/api/lanes":
                self.answered(200, {"lanes": board.model["work"]})
            elif where.path == "/api/lane":
                self.lane(asked["id"])
            elif found := PR_PATH.fullmatch(self.path):
                self.pull_request(f"{found[1]}#{found[2]}")
            elif found := THREAD_PATH.fullmatch(self.path):
                self.thread(found[1])
            elif self.path.startswith("/api/picture?"):
                self.picture(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["path"][0])
            elif self.path.startswith("/api/source?"):
                self.source(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)))
            elif found := FILE_PATH.fullmatch(self.path):
                self.file(found[1], found[2])
            elif self.path in FILES:
                file, kind = FILES[self.path]
                self.reply((SITE / file).read_bytes(), kind)
            elif self.path.startswith("/vendor/") and (file := (SITE / self.path.lstrip("/")).resolve()).is_relative_to(VENDOR) and file.suffix in VENDOR_KINDS and file.is_file():
                self.reply(file.read_bytes(), VENDOR_KINDS[file.suffix], "public, max-age=31536000, immutable")
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            host = self.headers.get("Host")
            page = f"{'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'}://{host}"
            if host not in hosts or self.headers.get("Origin") != page:
                self.send_error(403)
            elif self.path not in ("/api/answer", "/api/undo", "/api/say") and not REVIEW_PATH.fullmatch(self.path):
                self.send_error(404)
            elif not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif found := REVIEW_PATH.fullmatch(self.path):
                self.review(f"{found[1]}#{found[2]}")
            elif self.path == "/api/say":
                self.say(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            elif self.path == "/api/undo":
                self.undo(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["card"])
            else:
                self.answer(json.loads(self.rfile.read(int(self.headers["Content-Length"]))), f"{page}/")

        def card(self, answer: str) -> None:
            found = next((item for item in board.model["queue"] if item.get("answer") == answer), None)
            if not found:
                self.answered(404, {"error": GONE})
                return
            drawn = {path: json.loads(source.read_text()) for path in card_pictures(found["card"]) if (source := Path(path).with_name("source.json")).is_file()}
            self.answered(200, {"card": found["card"], "rail": found.get("rail"), "visuals": drawn})

        def task(self, order_id: str) -> None:
            found = next((entry for group in board.model["status"] for entry in group["features"] if entry["id"] == order_id), None)
            if found:
                self.answered(200, {"task": found})
            else:
                self.answered(404, {"error": f"{order_id} is no longer on the dashboard"})

        def thread(self, ts: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif ts not in board.threads:
                self.answered(404, {"error": f"{ts} is not the thread of a card of yours on this drive"})
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

        def source(self, query: dict[str, str]) -> None:
            cite, path, line, to = (query.get(field, "") for field in ("cite", "path", "line", "to"))
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif cite != board.repo and cite not in board.heads:
                self.answered(404, {"error": f"{cite} is neither this drive's repo nor one of its open PRs, so its code is not read here"})
            elif not REPO_FILE.fullmatch(path) or ".." in path.split("/") or not line.isdecimal() or not to.isdecimal():
                self.answered(400, {"error": "That is not a file and line in the repo."})
            else:
                repo, head = github.repo_of(cite), board.heads.get(cite)
                at = f"?ref={head}" if head else ""
                try:
                    text = github.run(["gh", "api", f"repos/{repo}/contents/{urllib.parse.quote(path)}{at}", "-H", "Accept: application/vnd.github.raw+json"], max_age="1h" if head else "60s")
                except github.GithubError:
                    where = f"at {head[:9]}" if head else "on its default branch"
                    self.answered(200, {"error": f"{repo} has no {path} {where}, or GitHub could not be reached."})
                    return
                self.answered(200, {"lines": artifacts.source_window(text.split("\n"), int(line), int(to))})

        def lane(self, job_id: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif not (job := state.load(board.name)["jobs"].get(job_id)):
                self.answered(404, {"error": f"{job_id} is no longer a job of this drive"})
            else:
                self.answered(200, {"newest": artifacts.newest_line(board.name, job_id, job)})

        def file(self, job_id: str, kind: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
                return
            job = state.load(board.name)["jobs"].get(job_id)
            if not job or kind not in artifacts.kept(job_id, job, board.reports):
                self.answered(404, {"error": f"The drive keeps no {artifacts.FILES.get(kind, kind).lower()} file for {job_id}."})
                return
            self.answered(200, artifacts.read(board.name, job_id, job, kind))

        def pull_request(self, key: str) -> None:
            if not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif key not in board.heads:
                self.answered(404, {"error": f"{key} is not an open PR of this drive"})
            elif read := pr_read(board.name, key):
                self.answered(200, read)
            else:
                self.answered(502, {"error": f"{key}: GitHub refused the read and it was never read before"})

        def review(self, key: str) -> None:
            if key not in board.heads:
                self.answered(404, {"error": f"{key} is not an open PR of this drive"})
                return
            try:
                self.answered(200, {"url": board.assistant.url(key, board.heads[key])})
            except (DriveError, install.InstallError) as error:
                self.answered(502, {"error": str(error)})

        def answer(self, asked: dict, via: str) -> None:
            """Hold his answer for UNDO_SECONDS, then send it unless an undo took it back first or the card was drawn again with another question or PR head meanwhile; cc-slack runs the card command on a sent question card answer as on his Slack tap, which ends the job's question and keeps it in History, and History keeps an undo held here as his decision."""
            card = asked["card"]
            if asked["label"] not in board.answers.get(card, []):
                self.answered(409, {"error": GONE})
                return
            shown = board.revisions[card]
            undone = threading.Event()
            with board.holding:
                if card in board.held:
                    self.answered(409, {"error": HELD})
                    return
                board.held[card] = undone
            undone.wait(UNDO_SECONDS)
            with board.holding:
                if board.held.pop(card, None) is not undone:
                    state.append_event(board.name, "undone", url=card, label=asked["label"], question=shown[0])
                    self.answered(200, {"undone": True})
                    return
            if card in board.revisions and board.revisions[card] != shown:
                self.answered(409, {"error": REDRAWN})
                return
            try:
                said = answer_card(card, asked["label"], via)
            except (DriveError, subprocess.TimeoutExpired) as error:
                self.answered(502, {"error": str(error)})
                return
            view.refresh_cards(board.name)
            self.answered(200, said)

        def undo(self, card: str) -> None:
            """Take back his answer: one still held never leaves, and one already sent reopens its card in Slack and the queue while cc-slack still shows its Undo, whose card request History keeps as his undo."""
            with board.holding:
                undone = board.held.pop(card, None)
            if undone:
                undone.set()
                self.answered(200, {"undone": True})
                return
            try:
                cc_slack("card-reopen", "--card", card)
            except (DriveError, subprocess.TimeoutExpired) as error:
                self.answered(409, {"error": str(error)})
                return
            view.refresh_cards(board.name)
            self.answered(200, {"undone": True, "restored": True})

        def say(self, typed: dict) -> None:
            text, under = typed["text"].strip(), typed["under"]
            if not text:
                self.answered(400, {"error": "there are no words to send"})
                return
            if under and under not in board.answers:
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

        def reply(self, body: bytes, kind: str, cache: str = "no-store") -> None:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Page


def serve(name: str, port: int, inherited: list[int]) -> None:
    """Serve the dashboard on loopback and this machine's own tailnet addresses, never a wildcard, the tailnet ones over HTTPS when the tailnet issues certificates, on the listening sockets an older install handed over when there are any. Once a newer long-running is installed, its drive takes this process's place on the same sockets, which queue each connection meanwhile, so the port answers throughout. Following the drive's files failing ends the server rather than freezing its page."""
    net = tailnet()
    board = Board(name, links_of(port, net))
    page = handler(port, net, board)
    certificate = Certificate(net.name) if net and net.https else None
    listening = [socket.socket(fileno=fd) for fd in inherited] or [
        socket.create_server((address, port), family=socket.AF_INET6 if ":" in address else socket.AF_INET) for address in [LOOPBACK] + (net.ips if net else [])
    ]
    servers = [Server(sock, page, certificate if sock.getsockname()[0] != LOOPBACK else None) for sock in listening]
    for server in servers:
        server.socket.set_inheritable(True)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def stopped(signum: int, frame: object) -> None:
        board.assistant.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stopped)
    newer = board.follow()
    for server in servers:
        server.shutdown()
    board.assistant.stop()
    print(f"handing port {port} to {newer}, a newer long-running", file=sys.stderr, flush=True)
    os.execv(sys.executable, [sys.executable, "-I", str(newer), "dashboard", "--drive", name, "--port", str(port), "--serve", *(f"--listening={sock.fileno()}" for sock in listening)])


def serving(name: str, port: int) -> dict | None:
    """The model the drive's port serves, or None when nothing answers there, an answer cut off as the old dashboard stops included."""
    try:
        with urllib.request.urlopen(url_of(port) + "api/view", timeout=30) as answer:
            drawn = json.load(answer)
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.IncompleteRead):
        return None
    if drawn.get("drive") != name:
        raise DriveError(f"port {port} serves something else; pass --port")
    return drawn


def await_newest(name: str, deadline: float) -> None:
    """Wait until the drive's dashboard runs the newest long-running installed, whose drive takes the dashboard's place on its port once the install wakes it; nothing to wait for while nothing serves the port, since its launch agent starts it on the newest. Refused at the deadline, naming the install it still runs."""
    port = port_of(name)
    while (drawn := serving(name, port)) and (runs := Path(drawn["code"]).parents[1]) != (newest := install.newest_drive().resolve().parents[1]):
        if install.clock() >= deadline:
            raise DriveError(f"the dashboard on port {port} still runs {runs}, not the newest long-running installed, {newest}")
        install.sleep(HANDOVER_POLL_SECONDS)


def wait_until(ready: Callable[[], bool], log: Path) -> None:
    deadline = time.monotonic() + START_SECONDS
    while not ready():
        if time.monotonic() > deadline:
            raise DriveError(f"the dashboard did not start; see {log}")
        time.sleep(0.2)


def agent(name: str) -> str:
    """The launch agent keeping the drive's dashboard, as launchctl names it in this user's login session."""
    return f"gui/{os.getuid()}/com.yasyf.long-running.dashboard.{name}"


def launchctl(*args: str) -> None:
    done = subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
    if done.returncode:
        raise DriveError(f"launchctl {' '.join(args)} exited {done.returncode}: {' '.join(done.stderr.split())}")


def kept(name: str) -> bool:
    return subprocess.run(["launchctl", "print", agent(name)], capture_output=True, check=False).returncode == 0


def keep(name: str, port: int) -> None:
    """Load the drive's launch agent, which starts its dashboard now and again RESTART_SECONDS after it exits, however it exits, on the newest long-running installed, found the way the drive's MCP server finds it."""
    domain, _, label = agent(name).rpartition("/")
    newest = json.loads(MANIFEST.read_text())["mcpServers"]["drive"]
    log = str(state.drive_dir(name) / "dashboard.log")
    path = LAUNCH_AGENTS / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": ["/usr/bin/env", newest["command"], *newest["args"][:-1], "dashboard", "--drive", name, "--port", str(port), "--serve"],
                "EnvironmentVariables": {variable: os.environ[variable] for variable in AGENT_ENV if variable in os.environ},
                "KeepAlive": True,
                "RunAtLoad": True,
                "ThrottleInterval": RESTART_SECONDS,
                "StandardOutPath": log,
                "StandardErrorPath": log,
            }
        )
    )
    launchctl("bootstrap", domain, str(path))


def start(name: str, port: int) -> list[str]:
    """The drive's dashboard links, tailnet first, its server kept by the drive's launch agent: one no agent keeps is stopped and the agent loaded, and a kept one not serving these links is started again at once. A proof's drive, whose state LONG_RUNNING_STATE moves, gets a server on this code and no agent, which would outlive the proof."""
    log = state.drive_dir(name) / "dashboard.log"
    drawn = serving(name, port)
    links = links_of(port, tailnet())
    keeping = not state.scratch() and kept(name)
    if drawn and drawn["links"] == links and (keeping or state.scratch() and drawn["code"] == CODE):
        return links
    if keeping:
        launchctl("kickstart", "-k", agent(name))
    else:
        if drawn:
            os.kill(drawn["pid"], signal.SIGTERM)
            wait_until(lambda: serving(name, port) is None, log)
        if state.scratch():
            with open(log, "a") as out:
                subprocess.Popen(
                    [sys.executable, "-I", str(DRIVE_BIN), "dashboard", "--drive", name, "--port", str(port), "--serve"],
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=out,
                    start_new_session=True,
                )
        else:
            keep(name, port)
    wait_until(lambda: serving(name, port) is not None, log)
    return serving(name, port)["links"]
