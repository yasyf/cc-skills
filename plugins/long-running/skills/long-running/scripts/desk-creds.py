#!/usr/bin/env python3
"""Run a desk on a remote Sprite host with short-lived credentials held in memory and never in a file, a record or an argument.

STDLIB ONLY. On the host, `serve` keeps each profile's credentials in memory and answers on 127.0.0.1, so a profile
whose `credential_process` is `desk-creds.py get <profile>` reads them and the AWS CLI and SDKs ask again once they
expire. On the machine whose AWS sign-in mints them, `push` exports each profile's session credentials with
`aws configure export-credentials` and hands them to the host's `serve` over `sprite exec`, through standard input
only; a supervised job runs it more often than they expire.

`install` moves a desk onto the Sprite runner.json's `host.sprite` names: it copies the pinned plugin version, the
drive's registry and the state beside runner.json, writes the host's runner.json with `host.landing` merged over
`landing`, starts `serve` as a Sprite service, loads the launchd job that pushes `host.aws_profiles` every 15
minutes and pushes them once, and starts the desk as a Sprite service whose environment holds each `host.env` name,
the value its command prints here, passed through standard input. That service runs `desk-creds.py run`, which
points each pushed profile's `credential_process` at `get` in `host.region`, names `host.github_app` for ccx's
GitHub reads, whose `private_key_command` reads the key at run time with those profiles, keeps the gh user
`host.gh_user`'s token from `DESK_GH_USER_TOKEN` for writes in a gh config under /dev/shm, sets `host.tz`, and
execs the desk runner.

usage:
  desk-creds.py serve [--port N]
  desk-creds.py get <profile> [<field>]
  desk-creds.py push --sprite <sprite> [--port N] <profile>...
  desk-creds.py install --config <runner.json> --desk <desk>
  desk-creds.py run --config <runner.json> --desk <desk>
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shlex
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 9181
HOME = "/home/sprite"
PLUGINS = Path.home() / ".claude/plugins/cache/skills/long-running"
SCRIPTS = Path(__file__).resolve().parent
USER_TOKEN = "DESK_GH_USER_TOKEN"
PUSH_SECONDS = 900
SHM = Path("/dev/shm")
FIELDS = ("Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")


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
            if set(FIELDS) - set(credentials) or expired(credentials):
                self.send_error(400, f"credentials need {', '.join(FIELDS)} and an Expiration still ahead")
                return
            with holding:
                held[self.path.strip("/")] = {name: credentials[name] for name in FIELDS}
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            print(f"desk-creds: {self.command} {self.path} {args[1] if len(args) > 1 else ''}", file=sys.stderr, flush=True)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def get(profile: str, field: str, port: int) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{profile}", timeout=10) as answer:
            credentials = json.load(answer)
    except urllib.error.URLError as error:
        print(f"desk-creds get: no credentials for {profile} on port {port}: {error}", file=sys.stderr)
        return 1
    print(credentials[field] if field else json.dumps(credentials))
    return 0


def push(sprite: str, port: int, profiles: list[str]) -> int:
    failed = 0
    for profile in profiles:
        exported = subprocess.run(["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"], capture_output=True, text=True)
        if exported.returncode:
            print(f"desk-creds push: {profile} did not export: {exported.stderr.strip()[-300:]}", file=sys.stderr)
            failed = 1
            continue
        sent = subprocess.run(
            ["sprite", "exec", "-s", sprite, "--", "curl", "-sS", "-f", "-X", "PUT", "-H", "Content-Type: application/json", "--data-binary", "@-", f"http://127.0.0.1:{port}/{profile}"],
            input=exported.stdout,
            capture_output=True,
            text=True,
        )
        if sent.returncode:
            print(f"desk-creds push: {profile} did not reach {sprite}: {sent.stderr.strip()[-300:]}", file=sys.stderr)
            failed = 1
            continue
        print(f"desk-creds push: {profile} on {sprite} until {json.loads(exported.stdout)['Expiration']}")
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


def keep_pushing(drive: str, sprite: str, profiles: list[str]) -> None:
    """Load the launchd job that pushes `profiles` to `sprite` every `PUSH_SECONDS`, unless it is loaded."""
    label = f"com.long-running.{drive}.desk-creds"
    if subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True).returncode == 0:
        return
    plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    log = str(Path.home() / ".claude/long-running" / f"{label}.log")
    plist.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(SCRIPTS.parents[2] / "bin/desk-creds.py"), "push", "--sprite", sprite, *profiles], "EnvironmentVariables": {"PATH": os.environ["PATH"]}, "StartInterval": PUSH_SECONDS, "RunAtLoad": True, "StandardOutPath": log, "StandardErrorPath": log}))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], check=True)


def install(config: Path, desk: str) -> int:
    raw = json.loads(config.read_text())
    host, version, state = raw["host"], raw["version"], config.parent
    sprite = host["sprite"]
    plugins = f"{HOME}/.claude/plugins/cache/skills/long-running"
    sprite_sh(sprite, 'mkdir -p "$1" && tar -xzf - -m -C "$1"', plugins, stdin=tar(PLUGINS, version))
    registry = Path.home() / ".claude/long-running/drives" / f"{raw['drive']}.json"
    sprite_sh(sprite, 'mkdir -p "$(dirname "$1")" && cat >"$1"', f"{HOME}/.claude/long-running/drives/{registry.name}", stdin=registry.read_bytes())
    files = [path.name for path in state.iterdir() if path.is_file() and path.suffix != ".log" and path != config]
    sprite_sh(sprite, 'mkdir -p "$1" && tar -xzf - -m -C "$1"', str(state), stdin=tar(state, *[name for name in ("runner", "cards") if (state / name).is_dir()], *files))
    sprite_sh(sprite, 'cat >"$1"', str(config), stdin=(json.dumps(host_config(raw), indent=2) + "\n").encode())
    entry = f"{plugins}/{version}/bin/desk-creds.py"
    sprite_sh(sprite, 'sprite-env services get desk-creds >/dev/null 2>&1 || sprite-env services create desk-creds --cmd "$1" --args serve --no-stream >/dev/null', entry)
    keep_pushing(raw["drive"], sprite, host["aws_profiles"])
    if failed := push(sprite, PORT, host["aws_profiles"]):
        return failed
    values = {name: subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip() for name, argv in host["env"].items()}
    if unfit := [name for name, value in values.items() if not value or "," in value]:
        print(f"desk-creds install: {', '.join(unfit)} printed nothing or a comma, which a Sprite service environment cannot hold; no service changed", file=sys.stderr)
        return 1
    sprite_sh(
        sprite,
        'env=$(paste -sd, -); sprite-env services delete "$1" >/dev/null 2>&1; sprite-env services create "$1" --cmd "$2" --args "run,--config,$3,--desk,$4" --env "$env" --needs desk-creds --no-stream >/dev/null',
        f"desk-{desk}",
        entry,
        str(config),
        desk,
        stdin="".join(f"{name}={value}\n" for name, value in values.items()).encode(),
    )
    print(f"desk-creds install: the {desk} desk runs on {sprite} as Sprite service desk-{desk} on {version}")
    return 0


def run(config: Path, desk: str) -> None:
    raw = json.loads(config.read_text())
    host = raw["host"]
    aws = Path.home() / ".aws/config"
    aws.parent.mkdir(parents=True, exist_ok=True)
    shim = shlex.quote(str(SCRIPTS.parents[2] / "bin/desk-creds.py"))
    aws.write_text("".join(f"[profile {profile}]\ncredential_process = {shim} get {profile}\nregion = {host['region']}\n\n" for profile in host["aws_profiles"]))
    ccx = Path.home() / ".config/ccx/github-app.toml"
    ccx.parent.mkdir(parents=True, exist_ok=True)
    ccx.write_text("".join(f"{key} = {json.dumps(value)}\n" for key, value in host["github_app"].items()))
    gh = SHM / f"{raw['drive']}-gh"
    gh.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = os.environ.pop(USER_TOKEN)
    hosts = gh / "hosts.yml"
    hosts.touch(mode=0o600)
    hosts.write_text(f"github.com:\n    users:\n        {host['gh_user']}:\n            oauth_token: {token}\n    git_protocol: https\n    oauth_token: {token}\n    user: {host['gh_user']}\n")
    os.environ.pop("GH_TOKEN", None)
    home = Path.home()
    os.environ |= {"GH_CONFIG_DIR": str(gh), "TZ": host["tz"], "CCX_CALLER": "desk-runner", "PATH": f"{home}/.local/bin:{home}/.bun/bin:/.sprite/bin:{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"}
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
    pushing.add_argument("--sprite", required=True)
    pushing.add_argument("--port", type=int, default=PORT)
    pushing.add_argument("profiles", nargs="+")
    for verb in ("install", "run"):
        desk = verbs.add_parser(verb)
        desk.add_argument("--config", type=Path, required=True)
        desk.add_argument("--desk", required=True)
    args = parser.parse_args()
    if args.verb == "serve":
        server(args.port).serve_forever()
        return 0
    if args.verb == "get":
        return get(args.profile, args.field, args.port)
    if args.verb == "install":
        return install(args.config, args.desk)
    if args.verb == "run":
        run(args.config, args.desk)
    return push(args.sprite, args.port, args.profiles)


if __name__ == "__main__":
    sys.exit(main())
