from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

from livedash import chat, context, layout, payloads, registry
from livedash.scheduler import Scheduler

SKILL = Path(__file__).resolve().parents[2]
PAGE = SKILL / "templates" / "live-dashboard.html"
CHAT_WIDGET = SKILL / "templates" / "live-dashboard-chat.html"
CONTEXT_FILE = "context.json"
SERVER_FILE = "server.json"
SERVER_LOG = "server.log"
START_LOCK = "start.lock"
TOKEN_HEADER = "X-Dashboard-Token"
ACTION_HEADER = "X-Dashboard-Action"
PORT_BASE = 8700
PORT_SPAN = 300
TICK_SECONDS = 1.0
RELOAD_SECONDS = 5.0
START_WAIT_SECONDS = 10.0
HEALTH_TIMEOUT_SECONDS = 2.0
LOOPBACK = {"127.0.0.1", "localhost"}
CCN_ID = re.compile(r"^[0-9a-f]{7,40}$")
ROW_LISTS = ("rows", "items", "entries", "tiles", "dists", "nodes")


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def preferred_port(ident: str) -> int:
    return PORT_BASE + int(hashlib.sha1(ident.encode()).hexdigest()[:8], 16) % PORT_SPAN


def read_facts(directory: Path) -> dict:
    path = directory / CONTEXT_FILE
    if not path.exists():
        raise SystemExit(f"{path} is missing; a drive writes it with drive.py, and `live-dashboard init` writes it outside a drive")
    facts = json.loads(path.read_text())
    return facts | {"packs": facts.get("packs") or {}}


def run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30).stdout


def tailnet_host() -> str | None:
    try:
        status = json.loads(run(["tailscale", "status", "--json"]))
    except FileNotFoundError:
        return None
    return status["Self"]["DNSName"].rstrip(".") if status.get("BackendState") == "Running" else None


def reachable(url: str, host: str) -> str:
    parts = urlsplit(url)
    if parts.hostname not in LOOPBACK:
        return url
    return parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host).geturl()


def on_tailnet(value, host: str):
    if isinstance(value, dict):
        return {key: reachable(item, host) if isinstance(item, str) and (key in ("url", "link") or key.endswith("_url")) else on_tailnet(item, host) for key, item in value.items()}
    if isinstance(value, list):
        return [on_tailnet(item, host) for item in value]
    return value


def url_of(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def share(port: int) -> str | None:
    target = f"127.0.0.1:{port}"
    try:
        if not (host := tailnet_host()):
            return None
        forwards = json.loads(run(["tailscale", "serve", "status", "--json"]) or "{}").get("TCP") or {}
        if (current := forwards.get(str(port), {}).get("TCPForward", target)) != target:
            print(f"not shared on the tailnet: port {port} already forwards to {current}", file=sys.stderr, flush=True)
            return None
        run(["tailscale", "serve", "--bg", "--yes", "--tcp", str(port), f"tcp://{target}"])
    except subprocess.CalledProcessError as failure:
        print(f"not shared on the tailnet: {(failure.stderr or '').strip() or failure}", file=sys.stderr, flush=True)
        return None
    return url_of(host, port)


def unshare(port: int) -> None:
    run(["tailscale", "serve", "--tcp", str(port), "off"])


def page(action_token: str) -> bytes:
    return PAGE.read_text().replace("</body>", CHAT_WIDGET.read_text() + "</body>").replace("{{action_token}}", action_token).encode()


def row_of(data: dict | None, key: str) -> dict | None:
    for name in ROW_LISTS:
        for row in (data or {}).get(name) or []:
            if isinstance(row, dict) and str(row.get("key")) == key:
                return row
    return None


class Dashboard(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], directory: Path, script: str):
        super().__init__(address, Handler)
        self.dir = directory
        self.script = script
        self.facts = read_facts(directory)
        registry.builtins()
        registry.load_packs(self.facts["packs"])
        self.scheduler = Scheduler(directory, self.facts)
        self.layout: layout.Layout | None = None
        self.layout_error: str | None = None
        self.seen: tuple = ()
        self.token = secrets.token_hex(16)
        self.chat_token = secrets.token_hex(16)
        self.action_token = secrets.token_hex(16)
        self.authorities = {f"127.0.0.1:{self.server_address[1]}", f"localhost:{self.server_address[1]}"}
        self.tailnet: str | None = None
        self.stopping = threading.Event()
        self.reloaded_at = 0.0

    def signature(self) -> tuple:
        files = [self.dir / CONTEXT_FILE, self.dir / layout.LAYOUT_FILE, *sorted((self.dir / "components").glob("*.py"))]
        return tuple((path.name, path.stat().st_mtime_ns) for path in files if path.exists())

    def reload(self) -> None:
        signature = self.signature()
        if signature == self.seen:
            return
        self.seen = signature
        self.facts = read_facts(self.dir)
        self.scheduler.facts = self.facts
        registry.load_local(self.dir)
        path = self.dir / layout.LAYOUT_FILE
        try:
            fresh = layout.load(self.dir, self.facts)
        except layout.LayoutError as failure:
            self.layout_error = failure.where(path)
            return
        except FileNotFoundError:
            self.layout_error = f"{path} is missing; write one with `live-dashboard init --dir {self.dir} --preset <name>`"
            return
        self.layout, self.layout_error = fresh, None
        self.scheduler.apply(fresh.cards)

    def poll(self) -> None:
        while not self.stopping.is_set():
            if time.monotonic() - self.reloaded_at >= RELOAD_SECONDS:
                self.reloaded_at = time.monotonic()
                self.reload()
            self.scheduler.tick()
            self.stopping.wait(TICK_SECONDS)

    def state(self) -> dict:
        cards = self.scheduler.envelopes(self.layout.cards) if self.layout else []
        if self.tailnet:
            cards = on_tailnet(cards, self.tailnet)
        sections = [{"title": section.title, "collapsed": section.collapsed, "cards": [card.id for card in section.cards]} for section in self.layout.sections] if self.layout else []
        return {
            "generated_at": iso(datetime.now(timezone.utc)),
            "dashboard": {"id": self.facts["id"], "title": self.layout.title if self.layout else self.facts.get("title"), "repo": self.facts.get("repo"), "program": self.facts.get("program")},
            "banner": self.layout.banner if self.layout else None,
            "layout_error": self.layout_error,
            "counts": {status: sum(card["status"] == status for card in cards) for status in ("error", "stale", "hung", "pending")},
            "sections": sections,
            "cards": cards,
        }

    def act(self, card: str, action: str, key: str, text: str) -> str:
        instance = self.scheduler.instance_of(card)
        if instance is None or action not in instance.spec.actions:
            raise LookupError(f"card {card} has no action {action}")
        row = row_of(instance.data, key)
        if row is None:
            raise LookupError(f"card {card} has no row {key}")
        ctx = context.Context(self.dir, self.facts, context.now(), instance.spec.timeout, instance.payload, self.scheduler.payload_of, self.scheduler.summary)
        message = instance.spec.actions[action](ctx, row, text)
        self.scheduler.refresh(card)
        return message


class Handler(BaseHTTPRequestHandler):
    server: Dashboard

    def log_message(self, format: str, *args: object) -> None:
        return

    def send(self, status: int, body: bytes, kind: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def text(self, status: int, body: str) -> None:
        self.send(status, body.encode(), "text/plain; charset=utf-8")

    def json(self, payload) -> None:
        self.send(200, json.dumps(payload, default=str).encode(), "application/json")

    @property
    def origin(self) -> str:
        return f"http://{self.headers['Host']}"

    def foreign(self) -> bool:
        if self.headers.get("Host") in self.server.authorities:
            return False
        self.text(421, "misdirected request\n")
        return True

    def trusted(self, header: str, token: str) -> bool:
        if secrets.compare_digest(self.headers.get(header, ""), token) and self.headers.get("Origin", self.origin) == self.origin:
            return True
        self.text(403, "forbidden\n")
        return False

    def sources(self) -> chat.Sources:
        facts = self.server.facts
        return chat.Sources(state=self.server.state(), state_dir=Path(facts.get("state_dir") or self.server.dir), ccn=chat.run_ccn(facts["checkout"]) if facts.get("checkout") else None)

    def card(self, ident: str) -> dict | None:
        return next((card for card in self.server.state()["cards"] if card["id"] == ident), None)

    def do_GET(self) -> None:
        if self.foreign():
            return
        url = urlparse(self.path)
        if url.path == "/":
            self.send(200, page(self.server.action_token), "text/html; charset=utf-8")
        elif url.path == "/state.json":
            body = json.dumps(self.server.state(), default=str).encode()
            tag = '"' + hashlib.sha1(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.end_headers()
            else:
                self.send(200, body, "application/json", {"ETag": tag})
        elif url.path == "/healthz":
            self.json({"drive": self.server.facts["id"], "script": self.server.script, "packs": self.server.facts["packs"], "pid": os.getpid()})
        elif url.path.startswith("/cards/") and url.path.endswith((".json", ".md")):
            ident, _, suffix = url.path.removeprefix("/cards/").rpartition(".")
            if (card := self.card(ident)) is None:
                self.text(404, f"no card {ident}\n")
            elif suffix == "json":
                self.json(card)
            else:
                self.text(200, f"## {card['title']}\n\n" + (payloads.markdown(card["kind"], card["payload"]) if card["payload"] else f"_{card['status']}: {card['error'] or 'no data yet'}_") + "\n")
        elif url.path.startswith("/ccn/") and CCN_ID.match(ident := url.path.removeprefix("/ccn/")) and self.server.facts.get("checkout"):
            try:
                self.text(200, run(["ccn", "-R", self.server.facts["checkout"], "show", ident]))
            except subprocess.CalledProcessError as failure:
                self.text(404, failure.stderr)
        elif url.path == "/ai.json":
            if chat.key():
                self.json(chat.site_config(self.server.chat_token))
            else:
                self.text(404, f"{chat.KEY_ENV} is not set in the dashboard's environment\n")
        elif url.path.startswith("/ask/"):
            self.ask(url.path.removeprefix("/ask/"), {name: values[0] for name, values in parse_qs(url.query).items()})
        else:
            self.text(404, "not found\n")

    def ask(self, verb: str, query: dict[str, str]) -> None:
        sources = self.sources()
        try:
            if verb == "digest":
                self.text(200, chat.digest(sources.state))
            elif verb == "search":
                self.json(chat.search(sources, query.get("query", "")))
            elif verb == "read":
                self.text(200, chat.read(sources, query.get("ref", "")))
            elif verb == "card":
                self.send(200, chat.card(sources.state, query.get("id")).encode(), "application/json")
            else:
                self.text(404, "not found\n")
        except (LookupError, ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
            self.text(404, f"{getattr(failure, 'stderr', '') or failure}".strip() + "\n")

    def relay(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.server.chat_token}" or self.headers.get("Origin", self.origin) != self.origin:
            self.text(403, "forbidden\n")
            return
        request = chat.relay(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            upstream = urllib.request.urlopen(request, timeout=chat.UPSTREAM_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as failure:
            self.send_response(failure.code)
            for name in ("Content-Type", "Retry-After"):
                if value := failure.headers.get(name):
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(failure.read())
            return
        with upstream:
            self.send_response(upstream.status)
            self.send_header("Content-Type", upstream.headers.get("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for chunk in chat.stream(upstream):
                self.wfile.write(chunk)
                self.wfile.flush()

    def do_POST(self) -> None:
        if self.foreign():
            return
        path = urlparse(self.path).path
        if path == "/ai/chat/completions":
            self.relay()
        elif path == "/shutdown":
            if self.trusted(TOKEN_HEADER, self.server.token):
                self.text(200, "stopping\n")
                self.server.stopping.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        elif path.startswith("/refresh/"):
            if self.trusted(ACTION_HEADER, self.server.action_token):
                found = self.server.scheduler.refresh(path.removeprefix("/refresh/"))
                self.text(202 if found else 404, "queued\n" if found else "no such card\n")
        elif path.startswith("/act/"):
            if self.trusted(ACTION_HEADER, self.server.action_token):
                self.action(*path.removeprefix("/act/").split("/", 1))
        else:
            self.text(404, "not found\n")

    def action(self, card: str, name: str) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        try:
            message = self.server.act(card, name, str(body.get("key", "")), " ".join(str(body.get("text") or "").split()))
        except LookupError as failure:
            self.text(404, f"{failure}\n")
        except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as failure:
            self.text(422 if isinstance(failure, ValueError) else 502, context.failure_text(failure) + "\n")
        else:
            self.json({"message": message})


def bind(host: str, port: int, directory: Path, script: str) -> Dashboard:
    try:
        return Dashboard((host, port), directory, script)
    except OSError:
        return Dashboard((host, 0), directory, script)


def write_record(directory: Path, record: dict) -> None:
    path = directory / SERVER_FILE
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(record, indent=2) + "\n")
    staged.replace(path)


def dashboard_url(record: dict) -> str:
    return record.get("tailnet_url", record["url"])


def serve(directory: Path, host: str, port: int | None, script: str) -> int:
    facts = read_facts(directory)
    server = bind(host, port or preferred_port(facts["id"]), directory, script)
    bound_host, bound_port = server.server_address[:2]
    record = {"drive": facts["id"], "pid": os.getpid(), "host": bound_host, "port": bound_port, "url": url_of(bound_host, bound_port), "script": script, "token": server.token, "started_at": iso(datetime.now(timezone.utc))}
    if tailnet_url := share(bound_port):
        record["tailnet_url"] = tailnet_url
        server.authorities.add(urlparse(tailnet_url).netloc)
        server.tailnet = urlparse(tailnet_url).hostname
    write_record(directory, record)
    threading.Thread(target=server.poll, daemon=True).start()
    print(dashboard_url(record), flush=True)
    server.serve_forever()
    if tailnet_url:
        unshare(bound_port)
    return 0


def health(record: dict) -> dict | None:
    try:
        with urllib.request.urlopen(f"{record['url']}healthz", timeout=HEALTH_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def running(directory: Path) -> dict | None:
    path = directory / SERVER_FILE
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    alive = health(record)
    return record | {"alive": alive} if alive and alive["drive"] == record["drive"] else None


def retire(record: dict) -> None:
    request = urllib.request.Request(f"{record['url']}shutdown", method="POST", data=b"", headers={TOKEN_HEADER: record["token"]})
    with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT_SECONDS):
        pass
    deadline = time.monotonic() + START_WAIT_SECONDS
    while health(record) and time.monotonic() < deadline:
        time.sleep(0.2)


def current(record: dict, script: str, packs: dict) -> bool:
    alive = record["alive"]
    return alive["script"] == script and alive.get("packs") == packs


def start(directory: Path, host: str, script: str) -> str:
    facts = read_facts(directory)
    with (directory / START_LOCK).open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        if record := running(directory):
            if current(record, script, facts["packs"]):
                return dashboard_url(record)
            retire(record)
        previous = (directory / SERVER_FILE).read_text() if (directory / SERVER_FILE).exists() else ""
        with (directory / SERVER_LOG).open("a") as sink:
            subprocess.Popen([sys.executable, script, "serve", "--dir", str(directory), "--host", host], stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, start_new_session=True, cwd=directory)
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline:
            path = directory / SERVER_FILE
            if path.exists() and path.read_text() != previous and (record := running(directory)):
                return dashboard_url(record)
            time.sleep(0.2)
    raise SystemExit(f"the dashboard did not come up within {START_WAIT_SECONDS:.0f}s; see {directory / SERVER_LOG}")


def stop(directory: Path) -> bool:
    if not (record := running(directory)):
        return False
    retire(record)
    return True
