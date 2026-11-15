import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


DIRECTORY = Path(__file__).resolve().parent
LOCK_PATH = DIRECTORY / "local-runtime-slot.lock"
OWNER_PATH = DIRECTORY / "local-runtime-slot-owner.private.json"


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_owner(record):
    descriptor = os.open(OWNER_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(record, output, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--queue", action="store_true", help="when the slot is busy, start a --wait copy in its own session, print its pid:<n> and exit 75")
    parser.add_argument("--wait", action="store_true", help="block until the slot frees instead of exiting 75")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if len(arguments.command) < 2 or arguments.command[0] != "--":
        parser.error("expected -- followed by an exact command and arguments")
    command = arguments.command[1:]
    descriptor = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if arguments.wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        print(f"with-local-runtime-slot: busy, held by {OWNER_PATH.read_text().strip()}", file=sys.stderr)
        if arguments.queue:
            queued(arguments.phase, command)
        return 75
    try:
        record = {
            "phase": arguments.phase,
            "wrapper_pid": os.getpid(),
            "executable": command[0],
            "argc": len(command),
            "state": "LOCKED",
            "acquired_at_utc": timestamp(),
        }
        write_owner(record)
        child = subprocess.Popen(command, shell=False, close_fds=True)
        record.update(child_pid=child.pid, state="RUNNING")
        try:
            write_owner(record)
        finally:
            returncode = child.wait()
        record.update(state="REAPED", child_returncode=returncode, completed_at_utc=timestamp())
        write_owner(record)
        if arguments.wait:
            print(f"with-local-runtime-slot: {arguments.phase} exited {returncode}", file=sys.stderr)
        return returncode if returncode >= 0 else 128 - returncode
    finally:
        os.close(descriptor)


def queued(phase, command):
    log_path = DIRECTORY / f"local-runtime-slot-queued-{os.getpid()}.log"
    with log_path.open("w") as log:
        waiter = subprocess.Popen(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--wait", "--phase", phase, "--", *command],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
        )
    print(f"pid:{waiter.pid}")
    print(f"with-local-runtime-slot: queued {phase} as pid:{waiter.pid} in its own session, which runs it once the slot frees, its output in {log_path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
