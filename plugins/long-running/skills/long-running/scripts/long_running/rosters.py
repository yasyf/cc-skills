from __future__ import annotations

import bisect
import errno
import json
import os
import re
import stat
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .roster_events import Event, WatchLost, native_events

UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
ROOT = ""


def binding(path: Path):
    try:
        value = path.lstat()
        return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid, os.readlink(path) if stat.S_ISLNK(value.st_mode) else None
    except OSError as error:
        return error.errno


def identity(path: Path):
    try:
        value = path.stat()
        return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid
    except OSError as error:
        return error.errno


@dataclass
class Watch:
    fd: int
    token: int
    inode: tuple[int, int]
    owners: dict[str, list] = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    active: bool = True


@dataclass
class Lane:
    teams: dict[Path, list] = field(default_factory=dict)
    ordered: list = field(default_factory=list)
    error: Exception | None = None

    def replace(self, team: Path, values: list) -> None:
        old = self.teams.pop(team, [])
        if values:
            self.teams[team] = values
        if self.error:
            self.error = None
            self.ordered = []
            pairs = [(value, owner) for owner, entries in self.teams.items() for value in entries]
        else:
            for value in old:
                pair = (value, team)
                self.ordered.pop(bisect.bisect_left(self.ordered, pair))
            pairs = [(value, team) for value in values]
        try:
            for pair in pairs:
                bisect.insort(self.ordered, pair)
        except TypeError as error:
            self.error = error.with_traceback(None)
            self.ordered = []


class RosterIndex:
    def __init__(self, config: Path, events=None):
        self.root = config / "teams"
        self.events = events if events is not None else native_events()
        self.watches = {}
        self.inodes = {}
        self.dependencies = {}
        self.projections = {}
        self.errors = {}
        self.lane_errors = {}
        self.team_lane_errors = {}
        self.lanes = {}
        self.root_identity = None
        self.failure = None
        self.work = defaultdict(int)
        try:
            self.refresh_root()
        except BaseException:
            self.close()
            raise

    def watch(self, fd: int, path: Path, owner: str, edge, dependencies: dict) -> int:
        value = os.fstat(fd)
        inode = value.st_dev, value.st_ino
        if inode in self.inodes and self.inodes[inode].active:
            os.close(fd)
            node = self.inodes[inode]
        else:
            try:
                token = self.events.add(fd, str(path))
            except BaseException:
                os.close(fd)
                raise
            node = Watch(fd, token, inode)
            self.inodes[inode] = node
            self.watches[token] = node
        dependencies.setdefault(node.token, []).append(edge)
        return node.token

    def open(self, path: Path, owner: str, dependencies: dict) -> int:
        parts = list(path.absolute().parts[1:])
        current = Path("/")
        links = 0
        while parts:
            name = parts.pop(0)
            if name in ("", "."):
                continue
            if name == "..":
                current = current.parent
                continue
            child = current / name
            flags = os.O_EVTONLY if sys.platform == "darwin" else os.O_RDONLY
            fd = os.open(current, flags | os.O_DIRECTORY | os.O_CLOEXEC)
            token = self.watch(fd, current, owner, None, dependencies)
            dependencies[token][-1] = (child, binding(child))
            value = child.lstat()
            if stat.S_ISLNK(value.st_mode):
                links += 1
                if links > 40:
                    raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(path))
                target = Path(os.readlink(child))
                if target.is_absolute():
                    current = Path("/")
                    parts = list(target.parts[1:]) + parts
                else:
                    parts = list(target.parts) + parts
            else:
                current = child
        flags = os.O_EVTONLY if sys.platform == "darwin" else os.O_RDONLY
        try:
            fd = os.open(current, flags | os.O_CLOEXEC)
        except PermissionError:
            value = current.stat()
            node = self.inodes.get((value.st_dev, value.st_ino))
            if node is not None and node.active:
                dependencies.setdefault(node.token, []).append(None)
            raise
        self.watch(fd, current, owner, None, dependencies)
        return os.open(current, os.O_RDONLY | os.O_CLOEXEC)

    def commit_dependencies(self, owner: str, dependencies: dict) -> None:
        previous = self.dependencies.get(owner, {})
        self.dependencies[owner] = dependencies
        for token, edges in previous.items():
            node = self.watches[token]
            for edge in set(edges):
                node.edges[edge].remove(owner)
                if not node.edges[edge]:
                    del node.edges[edge]
        for token, edges in dependencies.items():
            node = self.watches[token]
            node.owners[owner] = edges
            for edge in set(edges):
                node.edges.setdefault(edge, set()).add(owner)
        for token in previous.keys() - dependencies.keys():
            node = self.watches[token]
            del node.owners[owner]
        for token in previous.keys() - dependencies.keys():
            node = self.watches[token]
            if not node.owners:
                self.events.remove(token, node.fd)
                del self.watches[token]
                if self.inodes.get(node.inode) is node:
                    del self.inodes[node.inode]

    def replace(self, name: str, projection: dict, errors: dict) -> None:
        team = self.root / name
        old = self.projections.pop(name, {})
        for lane in self.team_lane_errors.pop(name, set()):
            self.lane_errors[lane].pop(name, None)
            if not self.lane_errors[lane]:
                del self.lane_errors[lane]
        if projection:
            self.projections[name] = projection
        for lane, error in errors.items():
            self.lane_errors.setdefault(lane, {})[name] = error
        if errors:
            self.team_lane_errors[name] = set(errors)
        for lane in old.keys() | projection.keys():
            entry = self.lanes.setdefault(lane, Lane())
            entry.replace(team, projection.get(lane, []))
            if not entry.teams:
                del self.lanes[lane]

    def refresh_team(self, name: str) -> None:
        dependencies = {}
        projection = defaultdict(list)
        lane_errors = {}
        self.errors.pop(name, None)
        path = self.root / name / "config.json"
        selected = os.path.lexists(path)
        try:
            fd = self.open(path, name, dependencies)
            with os.fdopen(fd) as handle:
                self.work["config_reads"] += 1
                text = handle.read()
                self.work["config_bytes"] += len(text.encode())
            self.work["json_decodes"] += 1
            for member in json.loads(text)["members"]:
                lane = member["name"]
                if isinstance(lane, str):
                    try:
                        projection[lane].append(member["joinedAt"])
                    except KeyError as error:
                        lane_errors[lane] = error.with_traceback(None)
        except (FileNotFoundError, NotADirectoryError) as error:
            if selected and path.is_symlink():
                self.errors[name] = error.with_traceback(None)
        except json.JSONDecodeError as error:
            self.errors[name] = json.JSONDecodeError(error.msg, "", 0)
        except OSError as error:
            if selected:
                self.errors[name] = error.with_traceback(None)
        except (ValueError, KeyError, TypeError) as error:
            self.errors[name] = error.with_traceback(None)
        except WatchLost:
            raise
        finally:
            self.commit_dependencies(name, dependencies)
        if name in self.errors:
            projection.clear()
        self.replace(name, dict(projection), lane_errors)

    def refresh_root(self) -> None:
        dependencies = {}
        names = set()
        try:
            fd = self.open(self.root, ROOT, dependencies)
            try:
                self.work["root_enumerations"] += 1
                with os.scandir(fd) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink() or entry.is_dir():
                                names.add(entry.name)
                        except OSError:
                            continue
            finally:
                os.close(fd)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            pass
        finally:
            self.commit_dependencies(ROOT, dependencies)
        self.root_identity = identity(self.root)
        existing = set(self.dependencies) - {ROOT}
        for name in existing - names:
            self.commit_dependencies(name, {})
            del self.dependencies[name]
            self.errors.pop(name, None)
            self.replace(name, {}, {})
        for name in names - existing:
            self.refresh_team(name)

    def reconcile(self, events: list[Event]) -> None:
        dirty = set()
        for event in events:
            node = self.watches.get(event.token)
            if node is None:
                continue
            if event.retired:
                node.active = False
            if event.authority:
                dirty.update(node.owners)
            else:
                for edge, owners in node.edges.items():
                    if edge is None:
                        if event.name is None or event.structural:
                            dirty.update(owners)
                    elif (event.name is None or edge[0].name == event.name) and binding(edge[0]) != edge[1]:
                        dirty.update(owners)
        if identity(self.root) != self.root_identity:
            dirty.add(ROOT)
            dirty.update(set(self.dependencies) - {ROOT})
        if ROOT in dirty:
            self.refresh_root()
        for name in dirty - {ROOT}:
            if name in self.dependencies:
                self.refresh_team(name)

    def lookup(self, lane: str) -> Path | None:
        if self.failure:
            raise self.failure.with_traceback(None)
        try:
            self.reconcile(self.events.cut())
        except BaseException as error:
            self.failure = error.with_traceback(None)
            raise
        if self.errors:
            raise next(iter(self.errors.values())).with_traceback(None)
        if lane in self.lane_errors:
            raise next(iter(self.lane_errors[lane].values())).with_traceback(None)
        entry = self.lanes.get(lane)
        if entry is None:
            return None
        if entry.error:
            raise entry.error.with_traceback(None)
        return entry.ordered[-1][1] / "inboxes" / f"{UNSAFE_NAME.sub('-', lane)}.json"

    def close(self) -> None:
        for node in self.watches.values():
            os.close(node.fd)
        self.watches.clear()
        self.inodes.clear()
        self.events.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
