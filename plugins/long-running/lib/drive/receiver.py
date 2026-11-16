import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import select
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from drive import dashboard, github, launchd, state
from drive.install import watch_upgraded as upgraded

PORT = 40990
FUNNEL_PORT = 8443
FUNNEL_PATH = "/github"
BUILDKITE_PATH = "/buildkite"
EVENTS = ["pull_request", "pull_request_review", "check_run", "status", "push", "workflow_run", "release"]
CARRYING = ("pull_request", "missed")
MARKS = ("ci", "release")
DELIVERY = "delivery"
REMERGES = ("push",)
BUILD_DELIVERY = "build delivery"
LABEL = f"{launchd.LABEL}.receiver"
WAIT_SECONDS = 5
SLEPT_SECONDS = 60
ANSWER_SECONDS = 10
MOST_BYTES = 25 * 1024 * 1024


def home() -> Path:
    """The receiver's folder, one for every drive on this Mac: the webhook secret GitHub's hooks and Buildkite's notification service sign with, the hooks it made and its log."""
    return state.root() / "receiver"


def log_path() -> Path:
    return home() / "receiver.log"


def deliveries_path() -> Path:
    """The receiver's log of every delivery it took, each line the delivery's own facts, which each drive's watch reads from where it last read to, matching them against the drive it loaded, so taking a delivery reads no drive."""
    return home() / "deliveries.jsonl"


def deliveries_end() -> int:
    return state.lines_end(deliveries_path())


def secret() -> str:
    """The secret every hook the receiver made signs its deliveries with, made once and readable by this user alone."""
    path = home() / "secret"
    if not path.exists():
        home().mkdir(parents=True, exist_ok=True)
        made = path.with_name(f"secret.{os.getpid()}")
        made.touch(0o600)
        made.write_text(secrets.token_hex(32))
        with contextlib.suppress(FileExistsError):
            os.link(made, path)
        made.unlink()
    return path.read_text()


def hooks() -> dict[str, dict]:
    """Each repository's hook the receiver made, by owner/name: its id, the events it sends, the GUID of the last delivery the receiver took from it, and the mark of each kind in MARKS, which moves with each delivery of that kind."""
    path = home() / "hooks.json"
    return json.loads(path.read_text()) if path.exists() else {}


@contextlib.contextmanager
def hooks_kept() -> Iterator[dict[str, dict]]:
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / "hooks.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        kept = hooks()
        yield kept
        made = home() / "hooks.json.tmp"
        made.write_text(json.dumps(kept))
        os.replace(made, home() / "hooks.json")


def unhooked(repos: set[str]) -> list[str]:
    """The repositories of repos with no hook of the receiver's sending EVENTS."""
    kept = hooks()
    return sorted(repo for repo in repos if (kept.get(repo) or {}).get("events") != EVENTS)


def url(path: str = FUNNEL_PATH) -> str:
    """A public address of the receiver: its Tailscale Funnel path on this Mac's tailnet name, /github for GitHub's hooks and /buildkite for Buildkite's notification service."""
    if not (net := dashboard.tailnet()):
        raise state.DriveError("tailscale does not run on this Mac, so GitHub and Buildkite have no address to deliver to")
    return f"https://{net.name}:{FUNNEL_PORT}{path}"


def asked(cmd: list[str], stdin: str | None = None) -> str:
    """One gh call of the receiver's upkeep, its failure naming the call, since gh's own message, such as `gh: Not Found (HTTP 404)`, names neither the endpoint nor the repository."""
    try:
        return github.run(cmd, stdin=stdin)
    except github.GithubError as error:
        raise github.GithubError(f"{shlex.join(cmd)}: {error}") from None


def hook(repo: str) -> bool:
    """Point a webhook of the repository at the receiver, signed with its secret and sending EVENTS: the one already at its address updated, else a new one, its marks moved, since the receiver heard nothing GitHub sent before it. False, with no hook made, for a repository GitHub lets this account no admin of, such as an upstream project a drive's PR is open on, whose hooks it answers 404 for."""
    if asked(["gh", "api", f"repos/{repo}", "--jq", ".permissions.admin"]).strip() != "true":
        return False
    address = url()
    config = json.dumps({"active": True, "events": EVENTS, "config": {"url": address, "content_type": "json", "secret": secret(), "insecure_ssl": "0"}})
    found = [made["id"] for made in json.loads(asked(["gh", "api", f"repos/{repo}/hooks?per_page=100"])) if made["config"].get("url") == address]
    method, path = ("PATCH", f"repos/{repo}/hooks/{found[0]}") if found else ("POST", f"repos/{repo}/hooks")
    made = json.loads(asked(["gh", "api", "-X", method, path, "--input", "-"], stdin=config))
    with hooks_kept() as kept:
        kept[repo] = {"id": made["id"], "last": (kept.get(repo) or {}).get("last"), "events": EVENTS} | dict.fromkeys(MARKS, moment())
    return True


def keep() -> None:
    """Load the receiver's launch agent unless one is loaded: launchd starts it now, at each login and again within RESTART_SECONDS of any exit, on the newest install, and each newer install takes its place on its socket, by main."""
    if launchd.kept(LABEL):
        return
    home().mkdir(parents=True, exist_ok=True)
    launchd.keep(LABEL, ["receive"], log_path(), {}, home())


def answering() -> bool:
    try:
        with urllib.request.urlopen(f"http://{dashboard.LOOPBACK}:{PORT}/", timeout=ANSWER_SECONDS) as answer:
            return answer.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def facts(event: str, payload: dict | None) -> dict:
    """What a delivery says that named matches a drive's PRs by: a PR's or its review's number, a check run's PRs and head, a commit status's head and the branches it is on, and the base a push moves."""
    match event:
        case "pull_request" | "pull_request_review":
            return {"numbers": [payload["pull_request"]["number"]]}
        case "check_run":
            return {"numbers": [pull["number"] for pull in payload["check_run"]["pull_requests"]], "head": payload["check_run"]["head_sha"]}
        case "status":
            return {"head": payload["sha"], "branches": [branch["name"] for branch in payload["branches"]]}
        case "push":
            return {"base": payload["ref"].removeprefix("refs/heads/")}
        case _:
            return {}


def named(delivery: dict, prs: dict[int, dict]) -> list[int]:
    """The numbers of the PRs of one drive in a delivery's repository, prs, that the delivery's facts name, its unmerged PRs and, for a delivery of CARRYING, the PRs it does not track that carry a stopped builder's parts: a PR's or its review's by number, a check run's by its PRs and its head, a commit status's by the head or branch it is on, and a push's by the base it moves, whose PRs GitHub merges again; a missed delivery names them all."""
    match delivery["event"]:
        case "pull_request" | "pull_request_review":
            found = set(delivery["numbers"])
        case "check_run":
            found = set(delivery["numbers"]) | {number for number, pr in prs.items() if pr["head"] == delivery["head"]}
        case "status":
            found = {number for number, pr in prs.items() if pr["head"] == delivery["head"] or pr.get("branch") in delivery["branches"]}
        case "push":
            found = {number for number, pr in prs.items() if pr.get("base") == delivery["base"]}
        case "missed":
            found = set(prs)
        case _:
            found = set()
    return sorted(found & prs.keys())


def marked(event: str, payload: dict | None) -> list[str]:
    """The kinds of MARKS a delivery moves: ci for a workflow run completing on its repository's default branch, release for any change to a release, and both for a missed delivery."""
    match event:
        case "workflow_run":
            return ["ci"] if payload["action"] == "completed" and payload["workflow_run"]["head_branch"] == payload["repository"]["default_branch"] else []
        case "release":
            return ["release"]
        case "missed":
            return list(MARKS)
        case _:
            return []


def heard(repo: str, kind: str) -> str | None:
    """The repository's mark of kind, which a wait reads GitHub again only once it moved; None while no hook of it sends that kind."""
    return (hooks().get(repo) or {}).get(kind)


def hear(repo: str, kind: str, since: str | None) -> None:
    """Return once the repository's mark of kind moved past since, as kqueue reports the receiver replacing hooks.json in its folder."""
    home().mkdir(parents=True, exist_ok=True)
    folder = os.open(home(), os.O_RDONLY)
    try:
        with contextlib.closing(select.kqueue()) as queue:
            queue.control([select.kevent(folder, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, select.KQ_NOTE_WRITE)], 0)
            while heard(repo, kind) == since:
                queue.control(None, 1)
    finally:
        os.close(folder)


def took(repo: str, hook_id: int, guid: str, event: str, payload: dict | None) -> None:
    """Append one delivery line of the delivery's own facts to the deliveries log, for each drive's watch to match against the PRs it tracks and read those from GitHub, keep the delivery as the hook's last and as the mark of each kind it moves, and log it. It reads no drive, so a delivery costs the same however many drives, PRs and jobs this Mac holds, and a burst of them leaves the receiver answering within GitHub's 10 seconds."""
    state.append_line(deliveries_path(), DELIVERY, repo=repo, event=event, guid=guid, **facts(event, payload))
    marks = marked(event, payload)
    with hooks_kept() as kept:
        kept[repo] = (kept.get(repo) or {}) | {"id": hook_id, "last": guid} | dict.fromkeys(marks, guid)
    print(f"{moment()} {event} {guid} {repo}{''.join(f'; marks {kind}' for kind in marks)}", flush=True)


def took_builds(event: str, links: list[str] | None) -> None:
    """Append one build delivery line naming the Buildkite builds a delivery of Buildkite's names to the deliveries log, for each drive's watch to read again those it holds, and log it; a missed delivery, links None, names every build each watch holds, since the receiver took none of what Buildkite sent while it was down."""
    state.append_line(deliveries_path(), BUILD_DELIVERY, builds=links, event=event)
    print(f"{moment()} buildkite {event}: {' '.join(links) if links is not None else 'every build a drive holds'}", flush=True)


def buildkite_signed(key: bytes, header: str, body: bytes) -> bool:
    """Whether a Buildkite delivery's X-Buildkite-Signature, `timestamp=<t>,signature=<hex>`, is the HMAC-SHA256 the webhook secret makes of `<t>.` and its body."""
    parts = dict(part.strip().split("=", 1) for part in header.split(",") if "=" in part)
    made = hmac.new(key, f"{parts.get('timestamp')}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(made, parts.get("signature", ""))


def caught_up() -> None:
    """Ask GitHub for each kept hook's newest delivery, and when it is newer than the last one taken, or its deliveries log is still empty, as for the first receiver that keeps one, whose predecessor wrote its deliveries into each drive's events file where no watch reads them now, take every open PR the drives track in its repository as delivered, since the receiver took none of what GitHub sent meanwhile: one read of those PRs in place of one call per missed delivery. A hook whose deliveries GitHub does not list, one deleted or a network not up yet, is forgotten, so the watch makes it again and owes a read of each PR of its repository."""
    unlogged = not deliveries_end()
    for repo, kept in hooks().items():
        try:
            newest = json.loads(asked(["gh", "api", f"repos/{repo}/hooks/{kept['id']}/deliveries?per_page=1"]))
        except github.GithubError as error:
            with hooks_kept() as hooked:
                hooked.pop(repo, None)
            print(f"{moment()} forgot {repo}'s hook {kept['id']}, which the watch makes again, reading its PRs: GitHub listed none of its deliveries: {error}", flush=True)
            continue
        if newest and (unlogged or newest[0]["guid"] != kept["last"]):
            took(repo, kept["id"], newest[0]["guid"], "missed", None)


def funnel() -> None:
    """Open the receiver's public paths through Tailscale Funnel, which persists in tailscaled, each proxied to the receiver's root, which tells GitHub's deliveries from Buildkite's by their headers; the rest of this Mac's serve config stays on the tailnet."""
    for path in (FUNNEL_PATH, BUILDKITE_PATH):
        done = subprocess.run(
            ["tailscale", "funnel", "--bg", "--yes", f"--https={FUNNEL_PORT}", f"--set-path={path}", f"http://{dashboard.LOOPBACK}:{PORT}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if done.returncode:
            raise state.DriveError(f"tailscale funnel exited {done.returncode}: {' '.join((done.stderr or done.stdout).split())}")


def moment() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def handler(key: bytes) -> type[BaseHTTPRequestHandler]:
    class Delivery(BaseHTTPRequestHandler):
        timeout = ANSWER_SECONDS

        def do_GET(self) -> None:
            self.answer(200, state.VERSION)

        def do_POST(self) -> None:
            body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), MOST_BYTES))
            if event := self.headers.get("X-Buildkite-Event"):
                self.buildkite(event, body)
                return
            signed = b"sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest().encode()
            guid = self.headers.get("X-GitHub-Delivery")
            if not hmac.compare_digest(signed, (self.headers.get("X-Hub-Signature-256") or "").encode()):
                print(f"{moment()} refused delivery {guid}: not signed with the webhook secret", flush=True)
                self.answer(401, "not signed with the webhook secret")
                return
            payload = json.loads(body)
            took(payload["repository"]["full_name"], int(self.headers["X-GitHub-Hook-ID"]), guid, self.headers["X-GitHub-Event"], payload)
            self.answer(200, "taken")

        def buildkite(self, event: str, body: bytes) -> None:
            if not buildkite_signed(key, self.headers.get("X-Buildkite-Signature") or "", body):
                print(f"{moment()} refused buildkite {event}: not signed with the webhook secret", flush=True)
                self.answer(401, "not signed with the webhook secret")
                return
            payload = json.loads(body)
            took_builds(event, [payload["build"]["web_url"]] if "build" in payload else [])
            self.answer(200, "taken")

        def answer(self, code: int, text: str) -> None:
            body = f"{text}\n".encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Delivery


def serving(listening: socket.socket, key: bytes) -> dashboard.Server:
    """The receiver's server on its listening socket, taking each delivery to its end on the one thread that accepts it, so a burst of deliveries starts no thread and waits in the socket's queue, each taken in the time one append takes."""
    server = dashboard.Server(listening, handler(key), None)
    server.process_request = server.process_request_thread
    server.timeout = WAIT_SECONDS
    return server


def main(inherited: int | None) -> int:
    """Take GitHub's and Buildkite's signed deliveries on loopback behind the Funnel paths, the process the receiver's launch agent runs. Started on its own, it listens before it opens the paths, asks GitHub what it missed and owes each drive a read of every Buildkite build it waits on, since Buildkite lists no deliveries, so a delivery landing meanwhile waits in the socket's queue, as one handed the socket does while its deliveries log is still empty, since the install before it wrote its deliveries into each drive's events file. Once a newer long-running is installed that runs other code than this one, as watch_upgraded reads it, it takes each delivery it accepted to its end and hands its listening socket to that install's drive, as the dashboard hands over its port, which serves on it at once and asks GitHub and Buildkite nothing, since the socket queued every delivery meanwhile, so the receiver runs each install's code and spends no GitHub read on it. It exits for launchd to start it again, which asks GitHub again, only once the clock jumps past SLEPT_SECONDS, as after this Mac slept."""
    os.environ.update(launchd.session_env())
    listening = socket.socket(fileno=inherited) if inherited is not None else socket.create_server((dashboard.LOOPBACK, PORT))
    server = serving(listening, secret().encode())
    if inherited is None:
        funnel()
    if inherited is None or not deliveries_end():
        caught_up()
        took_builds("missed", None)
    print(f"{moment()} receiving at {url()} and {url(BUILDKITE_PATH)} on long-running {state.VERSION}", flush=True)
    awake = time.time()
    while not (newer := upgraded()) and time.time() - awake < SLEPT_SECONDS:
        awake = time.time()
        server.handle_request()
    if not newer:
        return 0
    handed = listening.dup()
    server.server_close()
    handed.set_inheritable(True)
    print(f"{moment()} handing port {PORT} to {newer}, a newer long-running", flush=True)
    os.execv(sys.executable, [sys.executable, "-I", str(newer), "receive", f"--listening={handed.fileno()}"])
