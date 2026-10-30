from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .model import Incident

FORMAT = 1
NAMESPACE = "state-v1"


class InitializationRequired(RuntimeError):
    """The store needs explicit initialization of its dedicated root."""


class MigrationRequired(InitializationRequired):
    """Legacy state requires an explicit fenced import before SQLite use."""


class ResumeRequired(MigrationRequired):
    """An interrupted cutover requires explicit resumption."""


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(path: Path, record: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(record, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        staged.replace(path)
        _sync(path.parent)
    finally:
        staged.unlink(missing_ok=True)


def _without_acl(root: Path) -> None:
    if sys.platform == "darwin":
        shown = subprocess.run(["/bin/ls", "-lde", str(root)], check=True, capture_output=True, text=True)
        if any(re.match(r"\s*\d+:", line) for line in shown.stdout.splitlines()[1:]):
            raise ValueError("cutover requires a dedicated root without extended ACL entries")
    elif sys.platform == "linux":
        try:
            os.getxattr(root, "system.posix_acl_access")
        except OSError as error:
            if error.errno not in (errno.ENODATA, errno.ENOTSUP):
                raise
        else:
            raise ValueError("cutover requires a dedicated root without extended ACL entries")
    else:
        raise ValueError(f"unsupported directory fence platform: {sys.platform}")


def _root(root: Path) -> Path:
    root = root.resolve(strict=True)
    if not root.is_dir() or root.stat().st_uid != os.geteuid() or os.geteuid() == 0 or os.getuid() != os.geteuid():
        raise ValueError("cutover requires a dedicated directory owned by an unprivileged current user")
    _without_acl(root)
    return root


def _fenced(root: Path) -> bool:
    return os.getuid() == os.geteuid() != 0 and not stat.S_IMODE(root.stat().st_mode) & 0o222 and not os.access(root, os.W_OK)


def data_root(root: Path) -> Path:
    """Resolve a committed SQLite authority without changing the filesystem."""
    root = root.resolve()
    directory = root / NAMESPACE
    ready = directory / "READY.json"
    if not ready.is_file():
        if (directory / "FENCE.json").exists():
            raise ResumeRequired(f"resume the interrupted cutover of {root}")
        if any(root.glob("*.json")):
            raise MigrationRequired(f"migrate legacy incidents in {root}")
        raise InitializationRequired(f"initialize the dedicated store root {root}")
    manifest = json.loads(ready.read_text())
    if manifest["format"] != FORMAT or manifest["authority"] != "sqlite":
        raise ValueError(f"unsupported store authority in {ready}")
    if not _fenced(root):
        raise MigrationRequired(f"legacy publication directory is not fenced: {root}")
    return directory


@contextmanager
def _cutover(root: Path) -> Iterator[tuple[Path, Path]]:
    root = _root(root)
    directory = root / NAMESPACE
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError("SQLite namespace cannot be a symbolic link")
    _sync(root)
    with (directory / "CUTOVER.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root, directory


def _sources(root: Path) -> dict[str, tuple[str, Incident]]:
    found = {}
    for path in sorted(root.glob("*.json")):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"legacy incident must be a regular file: {path}")
        raw = path.read_bytes()
        incident = Incident.load(json.loads(raw))
        if incident.incident_id != path.stem:
            raise ValueError(f"legacy filename and incident identity differ: {path}")
        found[path.stem] = hashlib.sha256(raw).hexdigest(), incident
    return found


@contextmanager
def _legacy_fence(root: Path, directory: Path, resume: bool) -> Iterator[dict]:
    fence_path = directory / "FENCE.json"
    if fence_path.exists():
        if not resume:
            raise ResumeRequired(f"resume the interrupted cutover of {root}")
        fence = json.loads(fence_path.read_text())
        identity = root.stat()
        if fence["root"] != str(root) or (fence["device"], fence["inode"], fence["uid"]) != (identity.st_dev, identity.st_ino, identity.st_uid):
            raise ValueError("fenced root identity changed before resumption")
    else:
        if resume:
            raise ValueError(f"no interrupted cutover exists in {root}")
        if any(path.name != "CUTOVER.lock" for path in directory.iterdir()):
            raise ValueError("uncommitted SQLite namespace contains state without a cutover manifest")
        identity = root.stat()
        fence = {"format": FORMAT, "root": str(root), "device": identity.st_dev, "inode": identity.st_ino, "uid": identity.st_uid, "original_mode": stat.S_IMODE(identity.st_mode)}
        for source in root.glob("*.json"):
            with (root / f".{source.stem}.lock").open("a"):
                pass
        _publish(fence_path, fence)
        _sync(root)
    pending = directory / "PENDING.json"
    try:
        root.chmod(fence["original_mode"] & ~0o222)
        _sync(root)
        if not _fenced(root):
            raise PermissionError("directory fence does not exclude legacy publication")
        with ExitStack() as locks:
            for source in sorted(root.glob("*.json")):
                path = root / f".{source.stem}.lock"
                if not path.exists() or not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError(f"legacy lock must be a regular file: {path}")
                handle = locks.enter_context(path.open("a"))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield fence
    finally:
        if not (directory / "READY.json").exists() and not pending.exists():
            root.chmod(fence["original_mode"])
            _sync(root)
            fence_path.unlink(missing_ok=True)
            _sync(directory)


def initialize(root: Path) -> dict:
    """Initialize an empty dedicated root and exclude legacy JSON publishers."""
    root.mkdir(parents=True, exist_ok=True)
    with _cutover(root) as (root, directory):
        if (directory / "READY.json").exists():
            data_root(root)
            return json.loads((directory / "READY.json").read_text())
        if any(path.name != NAMESPACE for path in root.iterdir()):
            raise ValueError(f"initialize requires an empty dedicated root: {root}")
        with _legacy_fence(root, directory, False) as fence:
            if any(path.name != NAMESPACE for path in root.iterdir()):
                raise MigrationRequired(f"a legacy writer appeared during initialization of {root}")
            manifest = {"format": FORMAT, "authority": "sqlite", "source": {}, "fence": fence}
            _publish(directory / "READY.json", manifest)
            return manifest


def migrate(root: Path, importer: Callable[[Path, Incident], None], *, resume: bool = False) -> dict:
    """Import locked legacy snapshots, then atomically commit SQLite authority."""
    with _cutover(root) as (root, directory):
        if (directory / "READY.json").exists():
            raise ValueError(f"SQLite authority already committed in {root}")
        with _legacy_fence(root, directory, resume) as fence:
            sources = _sources(root)
            identities = {name: source[0] for name, source in sources.items()}
            if any(path.stem not in identities for path in directory.glob("*.sqlite3")):
                raise ValueError("uncommitted database is absent from the legacy source population")
            pending_path = directory / "PENDING.json"
            pending = {"format": FORMAT, "authority": "sqlite", "source": identities, "fence": fence}
            if pending_path.exists():
                if json.loads(pending_path.read_text()) != pending:
                    raise ValueError("legacy snapshots changed during interrupted cutover")
            else:
                _publish(pending_path, pending)
            for _, incident in sources.values():
                importer(directory, incident)
            if {name: source[0] for name, source in _sources(root).items()} != identities:
                raise ValueError("legacy snapshots changed before authority publication")
            _publish(directory / "READY.json", pending)
            pending_path.unlink()
            _sync(directory)
            return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicitly initialize or migrate a dedicated long-running state root")
    parser.add_argument("operation", choices=("initialize", "migrate", "resume"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    if args.operation == "initialize":
        manifest = initialize(args.root)
    else:
        from .sqlite_store import import_database

        manifest = migrate(args.root, import_database, resume=args.operation == "resume")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
