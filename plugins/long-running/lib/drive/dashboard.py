import contextlib
import functools
import hashlib
import http.client
import json
import mimetypes
import os
import re
import select
import signal
import socket
import sqlite3
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
    launchd,
    pages,
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
CHANGE_POLL_SECONDS = 2
REBUILD_GAP_SECONDS = 3
HEARD_SINCE = "1d"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/visuals.js": ("visuals.js", "text/javascript; charset=utf-8"),
}
VENDOR = SITE / "vendor"
KIT = SITE / "kit"
SHARED = (("Access-Control-Allow-Origin", "*"),)
SANDBOXED = (("Content-Security-Policy", "sandbox allow-scripts"),)
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
PAGE_PATH = re.compile(r"/pages/([^/]+)/(.*)")
REPO_FILE = re.compile(r"[\w.@-]+(?:/[\w.@-]+)+")
BYTE_RANGE = re.compile(r"bytes=(\d+)-(\d*)")
NOT_OWNER = "only the drive's owner can do this, and this device is not signed in to the tailnet as him"
GONE = "this card no longer waits on that answer"
ELSEWHERE = "that is not a card thread of this drive"
MOVED = "the PR moved to a new head since your page drew it, so nothing was sent; read it again and tap it as it stands"
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


@functools.cache
def site_stamp() -> str:
    """The hash of every file under site/, which is all the page loads, so an install that changes none of them leaves each open page as it is."""
    digest = hashlib.sha256()
    for path in sorted(SITE.rglob("*")):
        if path.is_file():
            digest.update(f"{path.relative_to(SITE)}\0".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


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
    """Yield at once, then after each write kqueue reports to the drive's state directory, its database, the database's write-ahead log, its events log or Claude Code's plugins folder, where each install replaces installed_plugins.json, so an install wakes the dashboard as a change to the drive does, and at least every CHANGE_POLL_SECONDS, since a database write lands in its log before its commit is published to readers; a file deleted, as the last connection to the database deletes its log, is watched again once it is back."""
    queue = select.kqueue()
    watched: dict[Path, int] = {}
    while True:
        for path in (state.drive_dir(name), state.database_path(name), state.wal_path(name), state.events_path(name), install.config_dir() / "plugins"):
            if path in watched:
                continue
            try:
                watched[path] = os.open(path, os.O_RDONLY)
            except FileNotFoundError:
                continue
            flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_DELETE
            queue.control([select.kevent(watched[path], select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, flags)], 0)
        yield
        for event in queue.control(None, 1, CHANGE_POLL_SECONDS):
            if event.fflags & select.KQ_NOTE_DELETE:
                os.close(event.ident)
                watched = {path: fd for path, fd in watched.items() if fd != event.ident}


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
        self.heads: dict[str, str] = {}
        self.pictures: set[str] = set()
        self.threads: set[str] = set()
        self.reports: set[str] = set()
        self.reports_at = 0
        self.repo: str | None = None
        self.channel = ""
        self.assistant = review.Assistant(name)
        self.database: sqlite3.Connection | None = None
        self.heard: dict[str, list[dict]] = {}
        self.records = 0
        self.pages: tuple[int, list[dict]] = (0, [])

    def stamp(self) -> tuple[int, ...]:
        """What the board is built from, as it stands: the database's data_version on this board's own connection, which changes with each commit another connection makes, the time drive.json and his stored cards last changed, and how many events of the board's history the events log holds, each new one read here, so an event the board never shows rebuilds nothing."""
        if self.database is None and state.database_path(self.name).exists():
            self.database = state.connect(state.database_path(self.name))
        committed = self.database.execute("PRAGMA data_version").fetchone()[0] if self.database else 0
        self.read_history()
        paths = (state.state_path(self.name), state.cards_path(self.name), pages.pages_dir(self.name))
        return (committed, *(path.stat().st_mtime_ns if path.exists() else 0 for path in paths), len(self.events), self.records)

    def read_history(self) -> None:
        """Keep each event of the board's history and each of his words typed under a card the events log gained since the last read."""
        for event, end in state.read_events(self.name, self.offset):
            self.offset = end
            if event["kind"] in view.BOARD_EVENTS:
                self.events.append(event)

    def rebuild(self) -> None:
        self.read_history()
        found, source = view.stored_cards(self.name)
        folder = state.drive_dir(self.name) / "jobs"
        if (listed_at := folder.stat().st_mtime_ns if folder.exists() else 0) != self.reports_at:
            self.reports = {path.name.removesuffix(".replies.jsonl") for path in folder.glob("*.replies.jsonl")}
            self.reports_at = listed_at
        drive = state.load(self.name)
        repo = home_repo(drive["roles"]["coordinator"]["cwd"])
        with self.changed:
            heard = dict(self.heard)
        folder = pages.pages_dir(self.name)
        if (listed_at := folder.stat().st_mtime_ns if folder.exists() else 0) != self.pages[0]:
            self.pages = (listed_at, pages.listed(self.name))
        model = view.board(drive, found, source, self.events, state.now(), self.reports, heard) | {
            "code": CODE,
            "site": site_stamp(),
            "pid": os.getpid(),
            "links": self.links,
            "repo": repo,
            "pages": pages.shown(drive, self.pages[1]),
        }
        self.show(model)
        self.assistant.warm((entry["key"], entry["head"]) for entry in model["review"] if entry["group"] == "you")

    def hear(self) -> None:
        """Keep the newest HEARD_LINES records naming each order and job, by its order and job attrs and the ids its words name, from the last HEARD_SINCE of the drive's records and each `cci watch` delivers after, watching afresh RESTART_SECONDS after a watch ends."""
        while True:
            with self.changed:
                self.heard = {}
            with subprocess.Popen(["cci", "watch", "--drive", self.name, "--json", "--since", HEARD_SINCE], stdout=subprocess.PIPE, text=True) as watch:
                for line in watch.stdout:
                    record = json.loads(line)
                    attrs = record["fields"].get("attrs") or {}
                    named = {*view.NAMED.findall(record["text"]), *filter(None, (attrs.get("order"), attrs.get("job")))}
                    told = view.heard_line(record)
                    with self.changed:
                        for key in named:
                            self.heard[key] = [told, *self.heard.get(key, [])][: view.HEARD_LINES]
                        self.records += 1
            time.sleep(RESTART_SECONDS)

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
            self.heads = {entry["key"]: entry["head"] for entry in model["review"]}
            self.pictures = pictures
            self.threads = {item["thread"] for item in [*model["queue"], *model["review"]] if item.get("thread")}
            self.repo = model["repo"]
            self.channel = model["channel"]
            self.changed.notify_all()

    def follow(self) -> Path:
        """Rebuild on a change to the drive's stored state, at most once every REBUILD_GAP_SECONDS, so a burst of writes, as a busy drive commits about twice a second, costs one rebuild and a change inside the gap shows at the first wake after it, until a newer long-running is installed; then return that install's drive, so this code never reads a drive it saved."""
        built, due = None, 0.0
        for _ in changes(self.name):
            if newer := install.upgraded():
                return newer
            if time.monotonic() >= due and (stamp := self.stamp()) != built:
                built, due = stamp, time.monotonic() + REBUILD_GAP_SECONDS
                self.rebuild()

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
                self.reply((SITE / file).read_bytes(), kind, headers=SHARED)
            elif self.path.startswith("/vendor/") and (file := (SITE / self.path.lstrip("/")).resolve()).is_relative_to(VENDOR) and file.suffix in VENDOR_KINDS and file.is_file():
                self.reply(file.read_bytes(), VENDOR_KINDS[file.suffix], "public, max-age=31536000, immutable", SHARED)
            elif self.path.startswith("/kit/") and (file := (SITE / self.path.lstrip("/")).resolve()).is_relative_to(KIT) and file.suffix in VENDOR_KINDS and file.is_file():
                self.reply(file.read_bytes(), VENDOR_KINDS[file.suffix], headers=SHARED)
            elif (found := PAGE_PATH.fullmatch(where.path)) and (file := pages.file(board.name, found[1], urllib.parse.unquote(found[2]))):
                self.reply(file.read_bytes(), pages.KINDS[file.suffix], headers=SANDBOXED)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            host = self.headers.get("Host")
            page = f"{'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'}://{host}"
            if host not in hosts or self.headers.get("Origin") != page:
                self.send_error(403)
            elif self.path not in ("/api/answer", "/api/tap", "/api/undo", "/api/say") and not REVIEW_PATH.fullmatch(self.path):
                self.send_error(404)
            elif not owner_calling(self.client_address[0], net):
                self.answered(403, {"error": NOT_OWNER})
            elif found := REVIEW_PATH.fullmatch(self.path):
                self.review(f"{found[1]}#{found[2]}")
            elif self.path == "/api/say":
                self.say(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            elif self.path == "/api/undo":
                self.undo(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["card"])
            elif self.path == "/api/tap":
                self.tap(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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
                with Path(path).open("rb") as file:
                    size = os.fstat(file.fileno()).st_size
                    asked = BYTE_RANGE.fullmatch(self.headers.get("Range") or "")
                    first, last = (int(asked[1]), min(int(asked[2] or size - 1), size - 1)) if asked else (0, size - 1)
                    file.seek(first)
                    body = file.read(last - first + 1)
                ranged = (("Content-Range", f"bytes {first}-{last}/{size}"),) if asked else ()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.reply(body, mimetypes.guess_type(path)[0] or "application/octet-stream", headers=(("Accept-Ranges", "bytes"), *ranged), status=206 if asked else 200)

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
            """Send his answer at once, as his Slack tap does; cc-slack runs the card command on it, which ends the job's question and keeps it in History, and answers with the time its Undo shows until."""
            card = asked["card"]
            if asked["label"] not in board.answers.get(card, []):
                self.answered(409, {"error": GONE})
                return
            try:
                said = answer_card(card, asked["label"], via)
            except (DriveError, subprocess.TimeoutExpired) as error:
                self.answered(502, {"error": str(error)})
                return
            view.refresh_cards(board.name)
            self.answered(200, said)

        def tap(self, asked: dict) -> None:
            """Keep his Approve or Hold on a PR at once through the one tap recorder his Slack card's tap runs, which starts the lander on an Approve, unless the PR moved to another head since his page drew it; like his Slack PR card, a tap has no Undo. A PR with a Slack card is answered on that card instead, so the card closes."""
            key, label, head = asked["pr"], asked["label"], asked["head"]
            if key not in board.heads or label not in card.TAP_LABELS:
                self.answered(409, {"error": f"{key} is not an open PR of this drive waiting on Approve or Hold"})
                return
            if board.heads[key] != head:
                self.answered(409, {"error": MOVED})
                return
            repo, number = key.split("#")
            try:
                card.answered({"repo": repo, "number": int(number), "label": label, "heads": {key: head}, "source": "dashboard", "action_ts": None, "waits": ""})
            except DriveError as error:
                self.answered(502, {"error": str(error)})
                return
            self.answered(200, {"label": label, "at": state.stamp()})

        def undo(self, card: str) -> None:
            """Take back his sent answer, as his Slack Undo does: cc-slack reopens its card in Slack and the queue while it still shows its Undo, and its card command keeps the undo in History."""
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
                sent = relay(text, under)
            except (DriveError, subprocess.TimeoutExpired) as error:
                self.answered(502, {"error": str(error)})
                return
            if under:
                state.append_event(board.name, "asked", card=under, url=sent["permalink"])
            self.answered(200, sent)

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

        def reply(self, body: bytes, kind: str, cache: str = "no-store", headers: tuple[tuple[str, str], ...] = (), status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", cache)
            for header, value in headers:
                self.send_header(header, value)
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
    threading.Thread(target=board.hear, daemon=True).start()
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


def label(name: str) -> str:
    """The label of the launch agent keeping the drive's dashboard."""
    return f"{launchd.LABEL}.dashboard.{name}"


def start(name: str, port: int) -> list[str]:
    """The drive's dashboard links, tailnet first, its server kept by the drive's launch agent: one no agent keeps is stopped and the agent loaded, and a kept one not serving these links is started again at once. A proof's drive, whose state LONG_RUNNING_STATE moves, gets a server on this code and no agent, which would outlive the proof."""
    log = state.drive_dir(name) / "dashboard.log"
    drawn = serving(name, port)
    links = links_of(port, tailnet())
    keeping = not state.scratch() and launchd.kept(label(name))
    if drawn and drawn["links"] == links and (keeping or state.scratch() and drawn["code"] == CODE):
        return links
    if keeping:
        launchd.launchctl("kickstart", "-k", launchd.service(label(name)))
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
            launchd.keep(label(name), ["dashboard", "--drive", name, "--port", str(port), "--serve"], log, {}, Path("/"))
    wait_until(lambda: serving(name, port) is not None, log)
    return serving(name, port)["links"]
