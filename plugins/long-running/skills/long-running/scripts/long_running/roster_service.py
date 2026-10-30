from __future__ import annotations

import builtins
import fcntl
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .roster_events import WatchLost
from .rosters import RosterIndex

SOURCE = Path(__file__).resolve()
MODULES = ("roster_events.py", "rosters.py", "roster_service.py")
LOADED_IDENTITY = None


def source_snapshot() -> dict:
    sources = {name: (SOURCE.parent / name).read_bytes() for name in MODULES}
    digest = hashlib.sha256()
    for source in sources.values():
        digest.update(source)
    manifest = SOURCE.parents[4] / ".claude-plugin" / "plugin.json"
    version = json.loads(manifest.read_text())["version"] if manifest.is_file() else "0.1.0"
    identity = {"source": str(SOURCE), "fingerprint": digest.hexdigest(), "version": version}
    return {"identity": identity, "sources": {name: source.hex() for name, source in sources.items()}}


def source_identity() -> dict:
    return source_snapshot()["identity"]


def private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
        raise PermissionError(f"roster runtime directory is not private: {path}")


def runtime(root: Path) -> Path:
    base = Path("/tmp") / f"long-running-rosters-{os.getuid()}"
    private_directory(base)
    directory = base / hashlib.sha256(os.fsencode(root)).hexdigest()[:32]
    private_directory(directory)
    return directory


def lock_file(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1:
        os.close(fd)
        raise PermissionError(f"roster lock is not private: {path}")
    return fd


@contextmanager
def lifecycle(directory: Path, exclusive: bool):
    fd = lock_file(directory / "lifecycle")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


def receive(connection) -> dict:
    pieces = bytearray()
    while not pieces.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            raise ConnectionError("roster helper closed before its response")
        pieces.extend(chunk)
        if len(pieces) > 1048576:
            raise ValueError("roster protocol frame exceeds 1 MiB")
    value = json.loads(pieces)
    if not isinstance(value, dict):
        raise ValueError("roster protocol frame must be an object")
    return value


def send(connection, value: dict) -> None:
    connection.sendall(json.dumps(value, ensure_ascii=True).encode() + b"\n")


def connect(directory: Path):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(directory / "socket"))
        return connection
    except (FileNotFoundError, ConnectionRefusedError):
        connection.close()
        return None
    except BaseException:
        connection.close()
        raise


def handshake(connection, root: Path) -> dict:
    send(connection, {"verb": "identity", "root": str(root)})
    result = receive(connection)
    if result.get("root") != str(root):
        raise RuntimeError("roster helper config root mismatch")
    return result


def version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def current(incumbent: dict, desired: dict) -> bool:
    if incumbent["fingerprint"] == desired["fingerprint"]:
        return True
    if version(incumbent["version"]) > version(desired["version"]):
        return True
    if incumbent["version"] == desired["version"] and incumbent["source"] != desired["source"]:
        raise RuntimeError("different roster helper sources claim the same version")
    return False


def start(directory: Path, root: Path, owner: int) -> None:
    reader, writer = os.pipe()
    try:
        bootstrap = """import json, os, sys, types
from pathlib import Path
snapshot_fd = int(sys.argv.pop(1))
with os.fdopen(snapshot_fd) as handle:
    snapshot = json.load(handle)
package = types.ModuleType("long_running")
package.__path__ = [str(Path(snapshot["identity"]["source"]).parent)]
sys.modules[package.__name__] = package
for filename, source in snapshot["sources"].items():
    name = "long_running." + filename.removesuffix(".py")
    module = types.ModuleType(name)
    module.__file__ = str(Path(package.__path__[0]) / filename)
    module.__package__ = package.__name__
    sys.modules[name] = module
    setattr(package, name.rsplit(".", 1)[1], module)
    exec(compile(bytes.fromhex(source), module.__file__, "exec"), module.__dict__)
service = sys.modules["long_running.roster_service"]
service.LOADED_IDENTITY = snapshot["identity"]
service.main()
"""
        with tempfile.TemporaryFile() as snapshot:
            snapshot.write(json.dumps(source_snapshot()).encode())
            snapshot.seek(0)
            subprocess.Popen(
                [sys.executable, "-I", "-c", bootstrap, str(snapshot.fileno()), str(root), str(owner), str(writer)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(snapshot.fileno(), owner, writer),
                start_new_session=True,
            )
        os.close(writer)
        writer = -1
        handle = os.fdopen(reader, "rb")
        reader = -1
        with handle:
            line = handle.readline()
        if not line:
            raise RuntimeError("roster helper exited before readiness")
        result = json.loads(line)
        if result != {"ready": True}:
            raise RuntimeError(f"roster helper startup failed: {result}")
    finally:
        if reader != -1:
            os.close(reader)
        if writer != -1:
            os.close(writer)


def ensure(directory: Path, root: Path, desired: dict) -> None:
    with lifecycle(directory, True):
        incumbent = connect(directory)
        if incumbent is not None:
            with incumbent:
                if current(handshake(incumbent, root), desired):
                    return
                send(incumbent, {"verb": "retire"})
                if receive(incumbent) != {"retired": True}:
                    raise RuntimeError("roster helper refused replacement")
            owner = lock_file(directory / "owner")
            fcntl.flock(owner, fcntl.LOCK_EX)
        else:
            owner = lock_file(directory / "owner")
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(owner)
                raise RuntimeError("roster helper owns its lease without a reachable socket") from None
        try:
            start(directory, root, owner)
        finally:
            os.close(owner)


def raise_remote(value: dict) -> None:
    if value["type"] == "JSONDecodeError":
        raise json.JSONDecodeError(value["message"], "", 0)
    error = getattr(builtins, value["type"], None)
    if error not in (OSError, FileNotFoundError, NotADirectoryError, PermissionError, IsADirectoryError, ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise RuntimeError(value["message"])
    if error is UnicodeDecodeError:
        raise UnicodeError(value["message"])
    raise error(*value["args"])


def mailbox_of(lane: str) -> Path | None:
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    root = config.absolute()
    directory = runtime(root)
    desired = source_identity()
    with lifecycle(directory, False):
        connection = connect(directory)
        if connection is not None:
            with connection:
                if current(handshake(connection, root), desired):
                    send(connection, {"verb": "lookup", "lane": lane})
                    result = receive(connection)
                    if "error" in result:
                        raise_remote(result["error"])
                    return config / Path(result["path"]).relative_to(root) if result["path"] is not None else None
    ensure(directory, root, desired)
    with lifecycle(directory, False):
        connection = connect(directory)
        if connection is None:
            raise RuntimeError("ready roster helper has no socket")
        with connection:
            handshake(connection, root)
            send(connection, {"verb": "lookup", "lane": lane})
            result = receive(connection)
    if "error" in result:
        raise_remote(result["error"])
    return config / Path(result["path"]).relative_to(root) if result["path"] is not None else None


def retire(root: Path) -> None:
    directory = runtime(root)
    with lifecycle(directory, True):
        connection = connect(directory)
        if connection is not None:
            with connection:
                handshake(connection, root)
                send(connection, {"verb": "retire"})
                if receive(connection) != {"retired": True}:
                    raise RuntimeError("roster helper refused retirement")
        owner = lock_file(directory / "owner")
        try:
            fcntl.flock(owner, fcntl.LOCK_EX)
        finally:
            os.close(owner)


def serve(root: Path, owner: int, ready: int) -> None:
    directory = runtime(root)
    socket_path = directory / "socket"
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    index = None
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if LOADED_IDENTITY is None:
            raise RuntimeError("roster helper requires a compiled source snapshot")
        index = RosterIndex(root)
        info = {**LOADED_IDENTITY, "root": str(root), "pid": os.getpid()}
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen()
        os.write(ready, b'{"ready": true}\n')
        os.close(ready)
        ready = -1
        while True:
            connection, _ = listener.accept()
            with connection:
                try:
                    request = receive(connection)
                    if request != {"verb": "identity", "root": str(root)}:
                        raise ValueError("invalid roster handshake")
                    send(connection, info)
                    request = receive(connection)
                    if request == {"verb": "retire"}:
                        send(connection, {"retired": True})
                        return
                    if request.get("verb") != "lookup" or not isinstance(request.get("lane"), str):
                        raise ValueError("invalid roster request")
                    try:
                        if index is None:
                            index = RosterIndex(root)
                        path = index.lookup(request["lane"])
                        result = {"path": str(path) if path else None}
                    except (OSError, ValueError, KeyError, TypeError, WatchLost) as error:
                        args = list(error.args) if not isinstance(error, UnicodeDecodeError) else []
                        result = {"error": {"type": type(error).__name__, "message": str(error), "args": args}}
                        if isinstance(error, WatchLost) and index is not None:
                            index.close()
                            index = None
                    send(connection, result)
                except (ConnectionError, OSError, ValueError):
                    continue
    except BaseException as error:
        if ready != -1:
            os.write(ready, json.dumps({"error": str(error)}).encode() + b"\n")
        raise
    finally:
        listener.close()
        if index is not None:
            index.close()
        socket_path.unlink(missing_ok=True)
        os.close(owner)
        if ready != -1:
            os.close(ready)


def main() -> None:
    root, owner, ready = sys.argv[1:]
    serve(Path(root), int(owner), int(ready))


if __name__ == "__main__":
    main()
