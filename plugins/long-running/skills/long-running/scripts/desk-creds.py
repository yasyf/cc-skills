#!/usr/bin/env python3
"""Short-lived AWS credentials for a remote desk host, held in memory and never in a file, a record or an argument.

STDLIB ONLY. On the host, `serve` keeps each profile's credentials in memory and answers on 127.0.0.1, so a profile
whose `credential_process` is `desk-creds.py get <profile>` reads them and the AWS CLI and SDKs ask again once they
expire. On the machine whose AWS sign-in mints them, `push` exports each profile's session credentials with
`aws configure export-credentials` and hands them to the host's `serve` over `sprite exec`, through standard input
only; a supervised job runs it more often than they expire.

usage:
  desk-creds.py serve [--port N]
  desk-creds.py get <profile> [<field>]
  desk-creds.py push --sprite <sprite> [--port N] <profile>...
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9181
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
    args = parser.parse_args()
    if args.verb == "serve":
        server(args.port).serve_forever()
        return 0
    if args.verb == "get":
        return get(args.profile, args.field, args.port)
    return push(args.sprite, args.port, args.profiles)


if __name__ == "__main__":
    sys.exit(main())
