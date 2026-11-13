import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import select
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from drive import dashboard, github, launchd, state
from drive.install import upgraded

PORT = 40990
FUNNEL_PORT = 8443
FUNNEL_PATH = "/github"
EVENTS = ["pull_request", "pull_request_review", "check_run", "status", "push", "workflow_run", "release"]
MARKS = ("ci", "release")
DELIVERY = "delivery"
LABEL = f"{launchd.LABEL}.receiver"
WAIT_SECONDS = 5
SLEPT_SECONDS = 60
ANSWER_SECONDS = 10
MOST_BYTES = 25 * 1024 * 1024


def home() -> Path:
    """The receiver's folder, one for every drive on this Mac: its webhook secret, the hooks it made and its log."""
    return state.root() / "receiver"


def log_path() -> Path:
    return home() / "receiver.log"


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


def url() -> str:
    """The one public address of the receiver: its Tailscale Funnel path on this Mac's tailnet name."""
    if not (net := dashboard.tailnet()):
        raise state.DriveError("tailscale does not run on this Mac, so GitHub has no address to deliver to")
    return f"https://{net.name}:{FUNNEL_PORT}{FUNNEL_PATH}"


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
    """Load the receiver's launch agent unless one is loaded: launchd starts it now, at each login and again within RESTART_SECONDS of any exit, on the newest install."""
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


def named(event: str, payload: dict | None, prs: dict[int, dict]) -> list[int]:
    """The numbers of the open PRs one drive tracks in a delivery's repository, prs, that the delivery names: a PR's or its review's by number, a check run's by its PRs and its head, a commit status's by the head or branch it is on, and a push's by the base it moves, whose PRs GitHub merges again; a missed delivery names them all."""
    match event:
        case "pull_request" | "pull_request_review":
            found = {payload["pull_request"]["number"]}
        case "check_run":
            run = payload["check_run"]
            found = {pull["number"] for pull in run["pull_requests"]} | {number for number, pr in prs.items() if pr["head"] == run["head_sha"]}
        case "status":
            branches = {branch["name"] for branch in payload["branches"]}
            found = {number for number, pr in prs.items() if pr["head"] == payload["sha"] or pr.get("branch") in branches}
        case "push":
            found = {number for number, pr in prs.items() if pr.get("base") == payload["ref"].removeprefix("refs/heads/")}
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


def hear(repo: str, kind: str, since: str | None, seconds: float) -> None:
    """Return once the repository's mark of kind moved past since, as kqueue reports the receiver replacing hooks.json in its folder, or once seconds pass."""
    home().mkdir(parents=True, exist_ok=True)
    folder = os.open(home(), os.O_RDONLY)
    try:
        with contextlib.closing(select.kqueue()) as queue:
            queue.control([select.kevent(folder, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, select.KQ_NOTE_WRITE)], 0)
            end = time.monotonic() + seconds
            while heard(repo, kind) == since and (left := end - time.monotonic()) > 0:
                queue.control(None, 1, left)
    finally:
        os.close(folder)


def took(repo: str, hook_id: int, guid: str, event: str, payload: dict | None) -> None:
    """Append one delivery line to each drive's events file naming the PRs of its that the delivery names, for its watch to read from GitHub, keep the delivery as the hook's last and as the mark of each kind it moves, and log it."""
    said = []
    for name in state.names():
        prs = {int(key.partition("#")[2]): pr for key, pr in state.stored(name, ("prs",)).get("prs", {}).items() if key.partition("#")[0] == repo and pr["state"] == "open"}
        if numbers := named(event, payload, prs):
            state.append_event(name, DELIVERY, repo=repo, numbers=numbers, event=event, guid=guid)
            said.append(f"{name} {' '.join(f'#{number}' for number in numbers)}")
    marks = marked(event, payload)
    with hooks_kept() as kept:
        kept[repo] = (kept.get(repo) or {}) | {"id": hook_id, "last": guid} | dict.fromkeys(marks, guid)
    print(f"{moment()} {event} {guid} {repo}: {'; '.join([*said, *(f'marks {kind}' for kind in marks)]) or 'no open PR a drive tracks'}", flush=True)


def caught_up() -> None:
    """Ask GitHub for each kept hook's newest delivery, and when it is newer than the last one taken, take every open PR the drives track in its repository as delivered, since the receiver took none of what GitHub sent meanwhile: one read of those PRs in place of one call per missed delivery. A hook whose deliveries GitHub does not list, one deleted or a network not up yet, is forgotten, so the watch makes it again and owes a read of each PR of its repository."""
    for repo, kept in hooks().items():
        try:
            newest = json.loads(asked(["gh", "api", f"repos/{repo}/hooks/{kept['id']}/deliveries?per_page=1"]))
        except github.GithubError as error:
            with hooks_kept() as hooked:
                hooked.pop(repo, None)
            print(f"{moment()} forgot {repo}'s hook {kept['id']}, which the watch makes again, reading its PRs: GitHub listed none of its deliveries: {error}", flush=True)
            continue
        if newest and newest[0]["guid"] != kept["last"]:
            took(repo, kept["id"], newest[0]["guid"], "missed", None)


def funnel() -> None:
    """Open the receiver's one public path through Tailscale Funnel, which persists in tailscaled; the rest of this Mac's serve config stays on the tailnet."""
    done = subprocess.run(
        ["tailscale", "funnel", "--bg", "--yes", f"--https={FUNNEL_PORT}", f"--set-path={FUNNEL_PATH}", f"http://{dashboard.LOOPBACK}:{PORT}"],
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
            signed = b"sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest().encode()
            guid = self.headers.get("X-GitHub-Delivery")
            if not hmac.compare_digest(signed, (self.headers.get("X-Hub-Signature-256") or "").encode()):
                print(f"{moment()} refused delivery {guid}: not signed with the webhook secret", flush=True)
                self.answer(401, "not signed with the webhook secret")
                return
            payload = json.loads(body)
            took(payload["repository"]["full_name"], int(self.headers["X-GitHub-Hook-ID"]), guid, self.headers["X-GitHub-Event"], payload)
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


def rewritten() -> bool:
    """Whether the newest long-running installed holds a receiver.py other than this one, the one install the receiver restarts for, since each start reads every kept hook's newest delivery from GitHub."""
    return bool((newer := upgraded()) and (newer.parents[1] / "lib" / "drive" / "receiver.py").read_bytes() != Path(__file__).read_bytes())


def main() -> int:
    """Take GitHub's signed deliveries on loopback behind the Funnel path, the process the receiver's launch agent runs. It listens before it opens the path and asks GitHub what it missed, so a delivery landing meanwhile waits in the socket's queue, then serves until the newest long-running installed runs another receiver.py, as rewritten reads it, or the clock jumps past SLEPT_SECONDS, as after this Mac slept, and exits for launchd to start it again, which asks GitHub again."""
    state.let_go_install()
    os.environ.update(launchd.session_env())
    server = ThreadingHTTPServer((dashboard.LOOPBACK, PORT), handler(secret().encode()))
    server.daemon_threads = True
    server.timeout = WAIT_SECONDS
    funnel()
    caught_up()
    print(f"{moment()} receiving at {url()} on long-running {state.VERSION}", flush=True)
    awake = time.time()
    while not rewritten() and time.time() - awake < SLEPT_SECONDS:
        awake = time.time()
        server.handle_request()
    return 0
