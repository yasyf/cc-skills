#!/usr/bin/env python3
"""Run a desk on a remote Sprite host whose credentials are short-lived or held in memory, never in a file, a record, an argument or a Sprite service's environment.

STDLIB ONLY. On the host, `serve` keeps each profile's credentials and each pushed secret in memory, answers on
127.0.0.1, and holds a Sprite task that it refreshes every minute, so the host never pauses while it serves. A profile
whose `credential_process` is `desk-creds.py get <profile>` reads them and the AWS CLI and SDKs ask again once they
expire. On the machine whose AWS sign-in mints them, `push` exports each `host.aws_profiles` profile's session
credentials with `aws configure export-credentials`, runs each `host.secrets` command for the value it prints, and
hands them to the host's `serve` over `sprite exec`, through standard input only, each secret expiring after three
pushes; a supervised job runs it every 15 minutes.

`install` moves a desk onto the Sprite runner.json's `host.sprite` names: it copies the pinned plugin version and the
drive's registry, the state beside runner.json only when the host holds no runner.json yet, writes the host's
runner.json with `host.landing` merged over `landing`, starts `serve` as a Sprite service, loads the launchd job that
pushes every 15 minutes and pushes once, and starts the desk as a Sprite service whose environment holds nothing.
That service runs `desk-creds.py run`, which points each pushed profile's `credential_process` at `get` in
`host.region`, names `host.github_app` for ccx's GitHub reads, whose `private_key_command` reads the key at run time
with those profiles, writes the gh user `host.gh_user`'s token, the pushed secret `GH_TOKEN`, into a gh config under
/dev/shm for writes, exports every other pushed secret, each `host.ssm` name the SSM parameter it maps to as
`host.ssm_profile` reads it and `host.tz`, and execs the desk runner, logging to the
`<desk>-runner.log` beside runner.json.

`roll` moves a running host desk onto runner.json's pinned `version` without stopping it mid-pass, the remote
`desk-runner.py handoff --desk`, whose release check needs the marketplace a Sprite never holds. Here it refuses
when that version's install is not its release or its main CI run has not passed, unless `--ci-red-ok` says why it
must run now, then copies the version and the host's runner.json over, points the host's `current` link at it, and
writes the desk runner's stop marker; the runner exits at the end of its pass, and the Sprite service restarts it
on that version within `ROLL_WAIT`.

usage:
  desk-creds.py serve [--port N]
  desk-creds.py get <profile> [<field>]
  desk-creds.py push --config <runner.json> [--port N]
  desk-creds.py install --config <runner.json> --desk <desk>
  desk-creds.py roll --config <runner.json> --desk <desk> [--ci-red-ok REASON]
  desk-creds.py run --config <runner.json> --desk <desk>
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from long_running import drive

PORT = 9181
HOME = "/home/sprite"
PLUGINS = Path.home() / ".claude/plugins/cache/skills/long-running"
SCRIPTS = Path(__file__).resolve().parent
USER_TOKEN = "GH_TOKEN"
PUSH_SECONDS = 900
SECRET = "secret/"
SHM = Path("/dev/shm")
OAUTH_TOKEN = re.compile(r"^(\s*oauth_token: ).*$", re.MULTILINE)
FIELDS = ("Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")
SECRET_FIELDS = ("Value", "Expiration")
AWAKE_TASK = "desk-creds"
AWAKE_SECONDS = 60
ROLL_WAIT = timedelta(minutes=20)
ROLL = """ln -sfn "$1" "$2"
for pid in $(pgrep -f "desk-runner[.]py run --config .* --desk $4\\$"); do echo "$pid" >"$3.stop.$pid"; done
deadline=$(($(date +%s) + $5))
until pgrep -f "$1/.*desk-runner[.]py run --config .* --desk $4\\$" >/dev/null; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "the $4 desk runner did not restart on $1 within $5 s" >&2; exit 1; }
  sleep 5
done"""


def expired(credentials: dict) -> bool:
    return datetime.fromisoformat(credentials["Expiration"]) <= datetime.now(timezone.utc)


def server(port: int) -> ThreadingHTTPServer:
    held: dict[str, dict] = {}
    holding = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with holding:
                credentials = held.get(self.path.strip("/"))
            if not credentials or expired(credentials):
                self.send_error(404, "no unexpired credentials were pushed for this profile")
                return
            body = json.dumps(credentials).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self) -> None:
            credentials = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = self.path.strip("/")
            fields = SECRET_FIELDS if key.startswith(SECRET) else FIELDS
            if set(fields) - set(credentials) or expired(credentials):
                self.send_error(400, f"{key} needs {', '.join(fields)} and an Expiration still ahead")
                return
            with holding:
                held[key] = {name: credentials[name] for name in fields}
                if key == f"{SECRET}{USER_TOKEN}":
                    keep_gh_token(credentials["Value"])
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            print(f"desk-creds: {self.command} {self.path} {args[1] if len(args) > 1 else ''}", file=sys.stderr, flush=True)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def keep_gh_token(token: str) -> None:
    """Write `token` into the gh config of each desk `run` started, so a desk runner older than the token it started with reads GitHub with the newest pushed one; gh and git's `gh auth git-credential` read that file at every call (root #335369: a runner's frozen token lapsed and each landing pass failed its fetch of the trunk)."""
    for hosts in SHM.glob("*-gh/hosts.yml"):
        staged = hosts.with_name(f".{hosts.name}.new")
        staged.unlink(missing_ok=True)
        staged.touch(mode=0o600)
        staged.write_text(OAUTH_TOKEN.sub(lambda found: found[1] + token, hosts.read_text()))
        staged.replace(hosts)

def hold_awake() -> None:
    """Keep this Sprite running while `serve` does: a Sprite pauses whenever no exec session is open, whatever its services do, unless a task holds it, so refresh one every `AWAKE_SECONDS` with five minutes to spare."""
    while True:
        held = subprocess.run(["/.sprite/bin/sprite-env", "curl", "-sSf", "-X", "PUT", f"/v1/tasks/{AWAKE_TASK}", "-H", "Content-Type: application/json", "-d", '{"expire": "5m"}'], capture_output=True, text=True)
        if held.returncode:
            print(f"desk-creds: the {AWAKE_TASK} task was not refreshed: {held.stderr.strip()[-300:]}", file=sys.stderr, flush=True)
        time.sleep(AWAKE_SECONDS)


def fetch(key: str, port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/{key}", timeout=10) as answer:
        return json.load(answer)


def get(profile: str, field: str, port: int) -> int:
    try:
        credentials = fetch(profile, port)
    except urllib.error.URLError as error:
        print(f"desk-creds get: no credentials for {profile} on port {port}: {error}", file=sys.stderr)
        return 1
    print(credentials[field] if field else json.dumps(credentials))
    return 0


def send(sprite: str, port: int, key: str, body: str) -> str:
    sent = subprocess.run(
        ["sprite", "exec", "-s", sprite, "--", "curl", "-sS", "-f", "-X", "PUT", "-H", "Content-Type: application/json", "--data-binary", "@-", f"http://127.0.0.1:{port}/{key}"],
        input=body,
        capture_output=True,
        text=True,
    )
    return sent.stderr.strip()[-300:] if sent.returncode else ""


def push(config: Path, port: int) -> int:
    host = json.loads(config.read_text())["host"]
    sprite, failed = host["sprite"], 0
    for profile in host["aws_profiles"]:
        exported = subprocess.run(["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"], capture_output=True, text=True)
        if exported.returncode:
            print(f"desk-creds push: {profile} did not export: {exported.stderr.strip()[-300:]}", file=sys.stderr)
            failed = 1
            continue
        if refused := send(sprite, port, profile, exported.stdout):
            print(f"desk-creds push: {profile} did not reach {sprite}: {refused}", file=sys.stderr)
            failed = 1
            continue
        print(f"desk-creds push: {profile} on {sprite} until {json.loads(exported.stdout)['Expiration']}")
    until = (datetime.now(timezone.utc) + timedelta(seconds=3 * PUSH_SECONDS)).isoformat(timespec="seconds")
    for name, argv in host["secrets"].items():
        printed = subprocess.run(argv, capture_output=True, text=True)
        if printed.returncode or not (value := printed.stdout.strip()):
            print(f"desk-creds push: {name}'s command printed nothing, exit {printed.returncode}", file=sys.stderr)
            failed = 1
            continue
        if refused := send(sprite, port, f"{SECRET}{name}", json.dumps({"Value": value, "Expiration": until})):
            print(f"desk-creds push: {name} did not reach {sprite}: {refused}", file=sys.stderr)
            failed = 1
            continue
        print(f"desk-creds push: {name} on {sprite} until {until}")
    return failed


def host_config(raw: dict) -> dict:
    """The runner.json the host reads: this one with `host.landing` merged over `landing` one level down."""
    landing = dict(raw["landing"])
    for key, value in raw["host"].get("landing", {}).items():
        landing[key] = {**landing.get(key, {}), **value} if isinstance(value, dict) else value
    return {**raw, "landing": landing}


def sprite_sh(sprite: str, script: str, *args: str, stdin: bytes | None = None) -> None:
    subprocess.run(["sprite", "exec", "-s", sprite, *([] if stdin is not None else ["--no-stdin"]), "--", "sh", "-c", script, "sh", *args], input=stdin, check=True)


def tar(root: Path, *names: str) -> bytes:
    return subprocess.run(["tar", "--no-xattrs", "--exclude=__pycache__", "--exclude=.in_use", "-C", str(root), "-czf", "-", *names], capture_output=True, check=True, env=os.environ | {"COPYFILE_DISABLE": "1"}).stdout


def keep_pushing(drive: str, config: Path) -> None:
    """Load the launchd job that runs `push` for `config` every `PUSH_SECONDS`, replacing a loaded one whose plist differs."""
    label = f"com.long-running.{drive}.desk-creds"
    plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    log = str(Path.home() / ".claude/long-running" / f"{label}.log")
    wanted = plistlib.dumps({"Label": label, "ProgramArguments": [str(SCRIPTS.parents[2] / "bin/desk-creds.py"), "push", "--config", str(config)], "EnvironmentVariables": {"PATH": os.environ["PATH"]}, "StartInterval": PUSH_SECONDS, "RunAtLoad": True, "StandardOutPath": log, "StandardErrorPath": log})
    loaded = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True).returncode == 0
    if loaded and plist.is_file() and plist.read_bytes() == wanted:
        return
    if loaded:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], check=True)
    plist.write_bytes(wanted)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], check=True)


def install(config: Path, desk: str) -> int:
    raw = json.loads(config.read_text())
    host, version, state = raw["host"], raw["version"], config.parent
    sprite = host["sprite"]
    plugins = f"{HOME}/.claude/plugins/cache/skills/long-running"
    sprite_sh(sprite, 'mkdir -p "$1" && tar -xzf - -m -C "$1"', plugins, stdin=tar(PLUGINS, version))
    registry = Path.home() / ".claude/long-running/drives" / f"{raw['drive']}.json"
    sprite_sh(sprite, 'mkdir -p "$(dirname "$1")" && cat >"$1"', f"{HOME}/.claude/long-running/drives/{registry.name}", stdin=registry.read_bytes())
    if subprocess.run(["sprite", "exec", "-s", sprite, "--no-stdin", "--", "test", "-f", str(config)]).returncode:
        files = [path.name for path in state.iterdir() if path.is_file() and path.suffix != ".log" and path != config]
        sprite_sh(sprite, 'mkdir -p "$1" && tar -xzf - -m -C "$1"', str(state), stdin=tar(state, *[name for name in ("runner", "cards") if (state / name).is_dir()], *files))
    sprite_sh(sprite, 'cat >"$1"', str(config), stdin=(json.dumps(host_config(raw), indent=2) + "\n").encode())
    entry = f"{plugins}/{version}/bin/desk-creds.py"
    sprite_sh(
        sprite,
        'sprite-env services delete "$1" >/dev/null 2>&1; sprite-env services delete desk-creds >/dev/null 2>&1; sprite-env services create desk-creds --cmd "$2" --args serve --no-stream >/dev/null && timeout 30 sh -c "until curl -so /dev/null http://127.0.0.1:$3/; do sleep 1; done"',
        f"desk-{desk}",
        entry,
        str(PORT),
    )
    keep_pushing(raw["drive"], config)
    failed = push(config, PORT)
    sprite_sh(
        sprite,
        'sprite-env services create "$1" --cmd "$2" --args "run,--config,$3,--desk,$4" --needs desk-creds --no-stream >/dev/null',
        f"desk-{desk}",
        entry,
        str(config),
        desk,
    )
    print(f"desk-creds install: the {desk} desk runs on {sprite} as Sprite service desk-{desk} on {version}{', waiting on the next push' if failed else ''}")
    return failed


def roll(config: Path, desk: str, ci_red_ok: str) -> int:
    raw = json.loads(config.read_text())
    sprite, version = raw["host"]["sprite"], raw["version"]
    if faults := drive.release_faults(version):
        print(f"desk-creds roll: nothing moved, since the {version} install is not its release: {'; '.join(faults[:3])}", file=sys.stderr)
        return 1
    if not ci_red_ok and (red := drive.ci_fault(version)):
        print(f"desk-creds roll: nothing moved, since {red}; roll once it passes, or pass --ci-red-ok with the reason it must run now", file=sys.stderr)
        return 1
    plugins = f"{HOME}/.claude/plugins/cache/skills/long-running"
    sprite_sh(sprite, 'mkdir -p "$1" && tar -xzf - -m -C "$1"', plugins, stdin=tar(PLUGINS, version))
    sprite_sh(sprite, 'cat >"$1"', str(config), stdin=(json.dumps(host_config(raw), indent=2) + "\n").encode())
    sprite_sh(sprite, ROLL, f"{plugins}/{version}", f"{HOME}/.claude/long-running/current", str(config), desk, str(int(ROLL_WAIT.total_seconds())))
    print(f"desk-creds roll: the {desk} desk on {sprite} runs {version}")
    return 0


def run(config: Path, desk: str) -> None:
    raw = json.loads(config.read_text())
    host = raw["host"]
    home = Path.home()
    aws = home / ".aws/config"
    aws.parent.mkdir(parents=True, exist_ok=True)
    shim = shlex.quote(str(SCRIPTS.parents[2] / "bin/desk-creds.py"))
    aws.write_text("".join(f"[profile {profile}]\ncredential_process = {shim} get {profile}\nregion = {host['region']}\n\n" for profile in host["aws_profiles"]))
    ccx = home / ".config/ccx/github-app.toml"
    ccx.parent.mkdir(parents=True, exist_ok=True)
    ccx.write_text("".join(f"{key} = {json.dumps(value)}\n" for key, value in host["github_app"].items()))
    os.environ |= {"HOME": str(home), "PATH": f"{home}/.local/bin:{home}/.bun/bin:/.sprite/bin:{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"}
    secrets = {name: fetch(f"{SECRET}{name}", PORT)["Value"] for name in host["secrets"]}
    token = secrets.pop(USER_TOKEN)
    ssm = {name: subprocess.run(["aws", "ssm", "get-parameter", "--profile", host["ssm_profile"], "--region", host["region"], "--name", path, "--with-decryption", "--query", "Parameter.Value", "--output", "text"], capture_output=True, text=True, check=True).stdout.strip() for name, path in host.get("ssm", {}).items()}
    gh = SHM / f"{raw['drive']}-gh"
    gh.mkdir(mode=0o700, parents=True, exist_ok=True)
    hosts = gh / "hosts.yml"
    hosts.touch(mode=0o600)
    hosts.write_text(f"github.com:\n    users:\n        {host['gh_user']}:\n            oauth_token: {token}\n    git_protocol: https\n    oauth_token: {token}\n    user: {host['gh_user']}\n")
    os.environ |= {**secrets, **ssm, "GH_CONFIG_DIR": str(gh), "TZ": host["tz"], "CCX_CALLER": "desk-runner"}
    log = os.open(config.parent / f"{desk}-runner.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.execv(sys.executable, [sys.executable, str(SCRIPTS / "desk-runner.py"), "run", "--config", str(config), "--desk", desk])


def main() -> int:
    parser = argparse.ArgumentParser(prog="desk-creds.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    serving = verbs.add_parser("serve")
    serving.add_argument("--port", type=int, default=PORT)
    getting = verbs.add_parser("get")
    getting.add_argument("profile")
    getting.add_argument("field", nargs="?", default="")
    getting.add_argument("--port", type=int, default=PORT)
    pushing = verbs.add_parser("push")
    pushing.add_argument("--config", type=Path, required=True)
    pushing.add_argument("--port", type=int, default=PORT)
    for verb in ("install", "run", "roll"):
        desk = verbs.add_parser(verb)
        desk.add_argument("--config", type=Path, required=True)
        desk.add_argument("--desk", required=True)
        if verb == "roll":
            desk.add_argument("--ci-red-ok", default="", metavar="REASON")
    args = parser.parse_args()
    if args.verb == "serve":
        threading.Thread(target=hold_awake, daemon=True).start()
        server(args.port).serve_forever()
        return 0
    if args.verb == "get":
        return get(args.profile, args.field, args.port)
    if args.verb == "install":
        return install(args.config, args.desk)
    if args.verb == "roll":
        return roll(args.config, args.desk, args.ci_red_ok)
    if args.verb == "run":
        run(args.config, args.desk)
    return push(args.config, args.port)


if __name__ == "__main__":
    sys.exit(main())
