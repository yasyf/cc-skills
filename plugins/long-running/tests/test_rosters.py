from __future__ import annotations

import ctypes
import errno
import threading
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from long_running import roster_events, roster_service, rosters


def write(root: Path, team: str, *members, prompt="") -> Path:
    path = root / "teams" / team / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"members": [{"name": name, "joinedAt": at, "prompt": prompt} for name, at in members]}))
    return path


def reference(root: Path, lane: str) -> Path | None:
    joined = [(member["joinedAt"], config.parent) for config in (root / "teams").glob("*/config.json") for member in json.loads(config.read_text())["members"] if member["name"] == lane]
    return max(joined)[1] / "inboxes" / f"{rosters.UNSAFE_NAME.sub('-', lane)}.json" if joined else None


def assert_lookup(index, root, *lanes):
    for lane in lanes:
        assert index.lookup(lane) == reference(root, lane)


@pytest.fixture
def helper(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    yield tmp_path
    roster_service.retire(tmp_path)


def test_native_index_exact_names_duplicates_ties_and_large_numbers(tmp_path):
    write(tmp_path, "a", ("desk.v2", 2**64), ("desk.v2", 2**64), ("desk-v2", 1))
    write(tmp_path, "z", ("desk.v2", 2**64), ("negative", -2**64))
    with rosters.RosterIndex(tmp_path) as index:
        assert_lookup(index, tmp_path, "desk.v2", "desk-v2", "negative", "missing")
        assert index.lookup("desk.v2").parent.parent.name == "z"
        assert len(index.lanes["desk.v2"].ordered) == 3


def test_warm_queries_never_open_decode_or_enumerate_the_corpus(tmp_path, monkeypatch):
    for number in range(128):
        write(tmp_path, str(number), ("desk", number), prompt="private" * 2048)
    with rosters.RosterIndex(tmp_path) as index:
        index.lookup("desk")
        before = dict(index.work)
        original_stat = Path.stat
        stats = []

        def stat(path, *args, **kwargs):
            stats.append(path)
            return original_stat(path, *args, **kwargs)

        def forbidden(*args, **kwargs):
            raise AssertionError("warm lookup touched roster contents or enumeration")

        monkeypatch.setattr(Path, "stat", stat)
        monkeypatch.setattr(os, "open", forbidden)
        monkeypatch.setattr(os, "scandir", forbidden)
        monkeypatch.setattr(json, "loads", forbidden)
        for _ in range(25):
            assert index.lookup("desk").name == "desk.json"
            assert index.lookup("absent") is None
        assert dict(index.work) == before
        assert stats == [tmp_path / "teams"] * 50


def test_one_roster_change_reads_only_its_projection(tmp_path):
    paths = [write(tmp_path, str(number), ("desk", number), prompt="secret" * 1000) for number in range(64)]
    with rosters.RosterIndex(tmp_path) as index:
        index.lookup("desk")
        before = dict(index.work)
        paths[0].write_text(json.dumps({"members": [{"name": "desk", "joinedAt": 1000}]}))
        assert_lookup(index, tmp_path, "desk")
        assert index.work["config_reads"] - before["config_reads"] == 1
        assert index.work["json_decodes"] - before["json_decodes"] == 1
        assert index.work["root_enumerations"] == before["root_enumerations"]


def test_atomic_config_replacement_and_deletion(tmp_path):
    config = write(tmp_path, "a", ("desk", 1))
    write(tmp_path, "b", ("desk", 2))
    with rosters.RosterIndex(tmp_path) as index:
        assert_lookup(index, tmp_path, "desk")
        replacement = config.with_suffix(".new")
        replacement.write_text(json.dumps({"members": [{"name": "desk", "joinedAt": 3}]}))
        replacement.replace(config)
        assert_lookup(index, tmp_path, "desk")
        config.unlink()
        assert_lookup(index, tmp_path, "desk")
        write(tmp_path, "a", ("desk", 4))
        assert_lookup(index, tmp_path, "desk")


def test_team_move_recreate_and_root_replacement(tmp_path):
    config = write(tmp_path, "a", ("desk", 10))
    write(tmp_path, "b", ("desk", 2))
    with rosters.RosterIndex(tmp_path) as index:
        config.parent.rename(tmp_path / "retired-team")
        assert_lookup(index, tmp_path, "desk")
        write(tmp_path, "a", ("desk", 20))
        assert_lookup(index, tmp_path, "desk")
        (tmp_path / "teams").rename(tmp_path / "retired-root")
        write(tmp_path, "a", ("desk", 1))
        assert_lookup(index, tmp_path, "desk")
        shutil.rmtree(tmp_path / "teams")
        assert_lookup(index, tmp_path, "desk")
        write(tmp_path, "new", ("desk", 30))
        assert_lookup(index, tmp_path, "desk")


def test_missing_root_and_new_team_are_discovered(tmp_path):
    with rosters.RosterIndex(tmp_path) as index:
        assert index.lookup("desk") is None
        write(tmp_path, "new", ("desk", 1))
        assert_lookup(index, tmp_path, "desk")
        write(tmp_path, "later", ("desk", 2))
        assert_lookup(index, tmp_path, "desk")


def test_external_symlink_ancestor_retarget(tmp_path):
    external = tmp_path / "external"
    old = external / "current"
    old.mkdir(parents=True)
    (old / "roster.json").write_text(json.dumps({"members": [{"name": "desk", "joinedAt": 1}]}))
    team = tmp_path / "teams" / "linked"
    team.mkdir(parents=True)
    (team / "config.json").symlink_to(old / "roster.json")
    with rosters.RosterIndex(tmp_path) as index:
        assert_lookup(index, tmp_path, "desk")
        old.rename(external / "retired")
        old.mkdir()
        (old / "roster.json").write_text(json.dumps({"members": [{"name": "new", "joinedAt": 2}]}))
        assert_lookup(index, tmp_path, "desk", "new")


def test_dangling_team_and_config_symlinks(tmp_path):
    root = tmp_path / "teams"
    root.mkdir()
    target = tmp_path / "external"
    (root / "linked").symlink_to(target, target_is_directory=True)
    with rosters.RosterIndex(tmp_path) as index:
        assert_lookup(index, tmp_path, "desk")
        target.mkdir()
        (target / "config.json").write_text(json.dumps({"members": [{"name": "desk", "joinedAt": 1}]}))
        assert_lookup(index, tmp_path, "desk")
        (target / "config.json").unlink()
        (target / "config.json").symlink_to(tmp_path / "absent.json")
        with pytest.raises(FileNotFoundError):
            reference(tmp_path, "desk")
        with pytest.raises(FileNotFoundError):
            index.lookup("desk")


def test_unrelated_malformed_roster_is_owned_until_repair_or_delete(tmp_path):
    write(tmp_path, "good", ("desk", 2))
    bad = write(tmp_path, "unrelated", ("other", 3))
    with rosters.RosterIndex(tmp_path) as index:
        bad.write_text("{")
        with pytest.raises(json.JSONDecodeError):
            index.lookup("desk")
        assert "unrelated" not in index.projections
        write(tmp_path, "unrelated", ("other", 4))
        assert_lookup(index, tmp_path, "desk")
        bad.write_text("{")
        with pytest.raises(json.JSONDecodeError):
            index.lookup("missing")
        bad.unlink()
        assert_lookup(index, tmp_path, "desk", "missing")


def test_joined_at_error_belongs_only_to_its_lane(tmp_path):
    config = write(tmp_path, "a", ("desk", 1))
    config.write_text(json.dumps({"members": [{"name": "desk", "joinedAt": 1}, {"name": "broken"}]}))
    with rosters.RosterIndex(tmp_path) as index:
        assert_lookup(index, tmp_path, "desk", "missing")
        with pytest.raises(KeyError):
            index.lookup("broken")
        config.write_text(json.dumps({"members": [{"name": "broken", "joinedAt": 3}]}))
        assert_lookup(index, tmp_path, "desk", "broken")


def test_incomparable_values_fail_only_the_affected_lane_and_recover(tmp_path):
    write(tmp_path, "a", ("desk", 1), ("good", 2))
    config = write(tmp_path, "b", ("desk", "different-type"))
    with rosters.RosterIndex(tmp_path) as index:
        with pytest.raises(TypeError):
            index.lookup("desk")
        assert_lookup(index, tmp_path, "good", "missing")
        config.unlink()
        assert_lookup(index, tmp_path, "desk", "good")


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_config_permission_error_invalidates_membership_and_recovers(tmp_path):
    config = write(tmp_path, "a", ("desk", 1))
    with rosters.RosterIndex(tmp_path) as index:
        assert_lookup(index, tmp_path, "desk")
        config.chmod(0)
        try:
            with pytest.raises(PermissionError):
                reference(tmp_path, "desk")
            with pytest.raises(PermissionError):
                index.lookup("desk")
            assert "a" not in index.projections
        finally:
            config.chmod(0o600)
        assert_lookup(index, tmp_path, "desk")


def test_complete_dirty_set_exceeds_initial_kqueue_capacity(tmp_path):
    paths = [write(tmp_path, str(number), ("desk", number)) for number in range(96)]
    with rosters.RosterIndex(tmp_path) as index:
        index.lookup("desk")
        before = index.work["config_reads"]
        for number, config in enumerate(paths):
            config.write_text(json.dumps({"members": [{"name": "desk", "joinedAt": 1000 - number}]}))
        assert_lookup(index, tmp_path, "desk")
        assert index.work["config_reads"] - before == 96
        if sys.platform == "darwin":
            assert index.events.capacity >= index.events.count > 96


def test_repeated_replacement_has_bounded_rows_and_watch_ownership(tmp_path):
    config = write(tmp_path, "a", ("desk", 1))
    with rosters.RosterIndex(tmp_path) as index:
        index.lookup("desk")
        counts = len(index.watches), len(index.lanes["desk"].ordered)
        for number in range(40):
            replacement = config.with_suffix(".next")
            replacement.write_text(json.dumps({"members": [{"name": "desk", "joinedAt": number}]}))
            replacement.replace(config)
            assert_lookup(index, tmp_path, "desk")
        assert (len(index.watches), len(index.lanes["desk"].ordered)) == counts
        assert all(node.owners for node in index.watches.values())


def test_captured_retired_tokens_cannot_refer_to_reused_descriptors(tmp_path):
    config = write(tmp_path, "a", ("old", 1))
    with rosters.RosterIndex(tmp_path) as index:
        node = next(node for node in index.watches.values() if node.inode == (config.stat().st_dev, config.stat().st_ino))
        captured = roster_events.Event(node.token, True)
        config.unlink()
        write(tmp_path, "a", ("new", 2))
        assert_lookup(index, tmp_path, "old", "new")
        assert node.token not in index.watches
        index.reconcile([captured])
        assert_lookup(index, tmp_path, "old", "new")


@pytest.mark.parametrize("boundary", ["parent-registration", "file-registration", "before-read"])
def test_watch_before_read_boundary_replacements(tmp_path, monkeypatch, boundary):
    config = write(tmp_path, "a", ("old", 1))
    fired = False
    original_watch = rosters.RosterIndex.watch
    original_fdopen = os.fdopen

    def replace():
        nonlocal fired
        fired = True
        temporary = config.with_suffix(".next")
        temporary.write_text(json.dumps({"members": [{"name": "new", "joinedAt": 2}]}))
        temporary.replace(config)

    def watch(self, fd, path, owner, edge, dependencies):
        token = original_watch(self, fd, path, owner, edge, dependencies)
        if not fired and owner == "a" and ((boundary == "parent-registration" and path == config.parent) or (boundary == "file-registration" and path == config)):
            replace()
        return token

    def fdopen(fd, *args, **kwargs):
        if not fired and boundary == "before-read":
            replace()
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(rosters.RosterIndex, "watch", watch)
    monkeypatch.setattr(os, "fdopen", fdopen)
    with rosters.RosterIndex(tmp_path) as index:
        assert fired
        assert_lookup(index, tmp_path, "old", "new")


def test_overflow_poisons_generation_instead_of_returning_last_good(tmp_path, monkeypatch):
    write(tmp_path, "a", ("desk", 1))
    with rosters.RosterIndex(tmp_path) as index:
        assert index.lookup("desk")

        def overflow():
            raise roster_events.WatchLost("injected overflow")

        monkeypatch.setattr(index.events, "cut", overflow)
        with pytest.raises(roster_events.WatchLost):
            index.lookup("desk")
        monkeypatch.setattr(index.events, "cut", lambda: [])
        with pytest.raises(roster_events.WatchLost):
            index.lookup("desk")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native event decoding")
def test_inotify_overflow_frame_invalidates_queue(monkeypatch):
    import struct

    events = roster_events.Inotify()
    try:
        original_ioctl = roster_events.fcntl.ioctl

        def ioctl(fd, operation, available, mutate):
            if fd == events.fd:
                available[0] = 16
            else:
                return original_ioctl(fd, operation, available, mutate)

        monkeypatch.setattr(roster_events.fcntl, "ioctl", ioctl)
        monkeypatch.setattr(roster_events.os, "read", lambda fd, size: struct.pack("iIII", -1, events.OVERFLOW, 0, 0))
        with pytest.raises(roster_events.WatchLost):
            events.cut()
    finally:
        events.close()


def test_helper_concurrent_startup_uses_one_owner(helper):
    write(helper, "a", ("desk", 1))
    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(lambda _: roster_service.mailbox_of("desk"), range(36)))
    assert results == [reference(helper, "desk")] * 36
    directory = roster_service.runtime(helper)
    with roster_service.lifecycle(directory, False):
        with roster_service.connect(directory) as connection:
            info = roster_service.handshake(connection, helper)
    assert info["fingerprint"] == roster_service.source_identity()["fingerprint"]
    assert (directory / "socket").stat().st_mode & 0o777 == 0o600


def test_helper_restart_uses_fresh_offline_state(helper):
    write(helper, "a", ("old", 1))
    assert roster_service.mailbox_of("old") == reference(helper, "old")
    roster_service.retire(helper)
    write(helper, "a", ("new", 2))
    assert roster_service.mailbox_of("old") is None
    assert roster_service.mailbox_of("new") == reference(helper, "new")


def test_helper_preserves_remote_errors_and_repairs(helper):
    config = write(helper, "a", ("desk", 1))
    assert roster_service.mailbox_of("desk")
    config.write_text("{")
    with pytest.raises(json.JSONDecodeError):
        roster_service.mailbox_of("desk")
    write(helper, "a", ("new", 2))
    assert roster_service.mailbox_of("desk") is None
    assert roster_service.mailbox_of("new")


def test_helper_unreachable_live_lease_does_not_start_another_owner(helper):
    directory = roster_service.runtime(helper)
    fd = roster_service.lock_file(directory / "owner")
    roster_service.fcntl.flock(fd, roster_service.fcntl.LOCK_EX)
    try:
        with pytest.raises(RuntimeError, match="without a reachable socket"):
            roster_service.mailbox_of("desk")
    finally:
        os.close(fd)


def test_helper_source_update_drains_and_old_client_cannot_downgrade(helper, tmp_path):
    write(helper, "a", ("desk", 1))
    assert roster_service.mailbox_of("desk")
    directory = roster_service.runtime(helper)
    with roster_service.lifecycle(directory, False):
        with roster_service.connect(directory) as connection:
            old = roster_service.handshake(connection, helper)
    plugin = tmp_path / "updated-plugin"
    package = plugin / "skills" / "long-running" / "scripts" / "long_running"
    package.mkdir(parents=True)
    for name in (*roster_service.MODULES, "__init__.py"):
        shutil.copy(roster_service.SOURCE.parent / name, package / name)
    (package / "rosters.py").write_text((package / "rosters.py").read_text() + "\nUPDATE_IDENTITY = True\n")
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "999.0.0"}))
    code = "from long_running.roster_service import mailbox_of; print(mailbox_of('desk'))"
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(package.parent)}, capture_output=True, text=True, check=True, timeout=30)
    assert result.stdout.strip() == str(reference(helper, "desk"))
    assert roster_service.mailbox_of("desk") == reference(helper, "desk")
    with roster_service.lifecycle(directory, False):
        with roster_service.connect(directory) as connection:
            new = roster_service.handshake(connection, helper)
    assert new["pid"] != old["pid"]
    assert new["version"] == "999.0.0"


def test_private_runtime_rejects_symlinks_and_public_permissions(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PermissionError):
        roster_service.private_directory(link)
    target.chmod(0o755)
    with pytest.raises(PermissionError):
        roster_service.private_directory(target)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin SDK ABI")
def test_kevent_layout_matches_installed_darwin_sdk(tmp_path):
    source = tmp_path / "layout.c"
    source.write_text('#include <sys/event.h>\n#include <stddef.h>\n#include <stdio.h>\nint main(void) { printf("%zu %zu %zu %zu %zu %zu %zu\\n", sizeof(struct kevent), offsetof(struct kevent, ident), offsetof(struct kevent, filter), offsetof(struct kevent, flags), offsetof(struct kevent, fflags), offsetof(struct kevent, data), offsetof(struct kevent, udata)); }\n')
    executable = tmp_path / "layout"
    subprocess.run(["cc", str(source), "-o", str(executable)], check=True)
    output = subprocess.check_output([str(executable)], text=True)
    layout = [ctypes.sizeof(roster_events.Kevent), *(getattr(roster_events.Kevent, name).offset for name, _ in roster_events.Kevent._fields_)]
    assert list(map(int, output.split())) == layout


@pytest.mark.skipif(sys.platform != "linux", reason="Linux child event scope")
def test_unrelated_ancestor_child_chmod_never_reads_rosters(tmp_path, monkeypatch):
    root = tmp_path / "config"
    sibling = tmp_path / "unrelated"
    sibling.write_text("unrelated")
    for number in range(32):
        write(root, str(number), ("desk", number))
    with rosters.RosterIndex(root) as index:
        index.lookup("desk")
        before = dict(index.work)
        sibling.chmod(0o600)
        bindings = []
        original = rosters.binding

        def binding(path):
            bindings.append(path)
            return original(path)

        monkeypatch.setattr(rosters, "binding", binding)
        assert index.lookup("desk") == reference(root, "desk")
        assert dict(index.work) == before
        assert bindings == []


@pytest.mark.parametrize("warm", [False, True])
def test_cyclic_team_symlink_is_skipped_and_repaired(tmp_path, warm):
    write(tmp_path, "good", ("desk", 1))
    loop = tmp_path / "teams" / "loop"
    if not warm:
        loop.symlink_to("loop")
    with rosters.RosterIndex(tmp_path) as index:
        if warm:
            loop.symlink_to("loop")
        assert_lookup(index, tmp_path, "desk", "new")
        loop.unlink()
        write(tmp_path, "loop", ("new", 2))
        assert_lookup(index, tmp_path, "desk", "new")


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
@pytest.mark.parametrize("target", ["root", "team", "config", "symlink-target"])
@pytest.mark.parametrize("cold", [False, True])
def test_discovery_permissions_and_restoration_match_glob(tmp_path, target, cold):
    write(tmp_path, "good", ("desk", 1))
    config = write(tmp_path, "blocked", ("other", 2))
    blocked = {"root": tmp_path / "teams", "team": config.parent, "config": config}.get(target)
    if target == "symlink-target":
        blocked = tmp_path / "external"
        config.parent.rename(blocked)
        config.parent.symlink_to(blocked, target_is_directory=True)
    mode = blocked.stat().st_mode & 0o777
    if cold:
        blocked.chmod(0)
    try:
        with rosters.RosterIndex(tmp_path) as index:
            if not cold:
                assert_lookup(index, tmp_path, "desk", "other")
                blocked.chmod(0)
            if target == "config":
                with pytest.raises(PermissionError):
                    reference(tmp_path, "desk")
                with pytest.raises(PermissionError):
                    index.lookup("desk")
            else:
                assert_lookup(index, tmp_path, "desk", "other")
            blocked.chmod(mode)
            assert_lookup(index, tmp_path, "desk", "other")
    finally:
        blocked.chmod(mode)


def test_symlink_parent_traversal_preserves_logical_root(helper, monkeypatch):
    actual = helper / "actual"
    (actual / "child").mkdir(parents=True)
    (helper / "alias").symlink_to(actual / "child", target_is_directory=True)
    logical = helper / "alias" / ".." / "config"
    write(actual / "config", "correct", ("desk", 1))
    write(helper / "config", "wrong", ("desk", 2))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(logical))
    try:
        assert roster_service.mailbox_of("desk") == reference(logical, "desk")
        with rosters.RosterIndex(logical) as index:
            assert_lookup(index, logical, "desk")
    finally:
        roster_service.retire(logical)


@pytest.mark.parametrize("stage", ["before-handshake", "after-handshake", "result", "error"])
def test_aborted_client_preserves_owner_and_index(helper, stage):
    config = write(helper, "a", ("desk", 1))
    assert roster_service.mailbox_of("desk")
    directory = roster_service.runtime(helper)
    with roster_service.connect(directory) as connection:
        old = roster_service.handshake(connection, helper)
    with roster_service.connect(directory) as connection:
        if stage != "before-handshake":
            roster_service.handshake(connection, helper)
        if stage in ("result", "error"):
            if stage == "error":
                config.write_text("{")
            roster_service.send(connection, {"verb": "lookup", "lane": "desk"})
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    with roster_service.connect(directory) as connection:
        new = roster_service.handshake(connection, helper)
    assert new["pid"] == old["pid"]
    if stage == "error":
        write(helper, "a", ("desk", 1))
    assert roster_service.mailbox_of("desk") == reference(helper, "desk")


@pytest.mark.parametrize("stage", ["import", "bootstrap"])
def test_helper_identity_describes_compiled_snapshot_during_in_place_update(helper, stage):
    write(helper, "a", ("desk", 1))
    plugin = helper / "snapshot-plugin"
    package = plugin / "skills" / "long-running" / "scripts" / "long_running"
    package.mkdir(parents=True)
    for name in (*roster_service.MODULES, "__init__.py"):
        shutil.copy(roster_service.SOURCE.parent / name, package / name)
    path = package / "rosters.py"
    mutation = """
def replace_source():
    path = Path(__file__)
    path.write_text(path.read_text() + "\\nUPDATED_GENERATION = True\\n")
"""
    if stage == "import":
        mutation += '\nif "long_running.roster_service" not in sys.modules:\n    replace_source()\n'
    else:
        mutation += """
original_init = RosterIndex.__init__
def updated_init(self, *args, **kwargs):
    replace_source()
    original_init(self, *args, **kwargs)
RosterIndex.__init__ = updated_init
"""
    path.write_text(path.read_text() + mutation)
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "999.0.0"}))
    code = """import json, os
from pathlib import Path
from long_running import roster_service as service
root = Path(os.environ["CLAUDE_CONFIG_DIR"])
expected = service.source_identity()
service.mailbox_of("desk")
with service.connect(service.runtime(root)) as connection:
    actual = service.handshake(connection, root)
print(json.dumps({"expected": expected, "actual": actual, "disk": service.source_identity()}))
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(package.parent)}, capture_output=True, text=True, check=True, timeout=30)
    evidence = json.loads(result.stdout)
    assert evidence["actual"]["fingerprint"] == evidence["expected"]["fingerprint"]
    assert evidence["actual"]["fingerprint"] != evidence["disk"]["fingerprint"]


def test_replacement_waits_for_active_client_lifecycle_lock(helper):
    write(helper, "a", ("desk", 1))
    assert roster_service.mailbox_of("desk")
    directory = roster_service.runtime(helper)
    desired = {**roster_service.source_identity(), "fingerprint": "replacement-required"}
    waiting = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as workers:
        with roster_service.lifecycle(directory, False):
            with roster_service.connect(directory) as connection:
                old = roster_service.handshake(connection, helper)

                def replace():
                    waiting.set()
                    roster_service.ensure(directory, helper, desired)

                future = workers.submit(replace)
                assert waiting.wait(5)
                assert not future.done()
                roster_service.send(connection, {"verb": "lookup", "lane": "desk"})
                assert roster_service.receive(connection) == {"path": str(reference(helper, "desk"))}
        future.result(timeout=30)
    with roster_service.connect(directory) as connection:
        new = roster_service.handshake(connection, helper)
    assert new["pid"] != old["pid"]


def test_captured_token_cannot_target_reused_numeric_file_descriptor(tmp_path):
    config = write(tmp_path, "a", ("old", 1))
    with rosters.RosterIndex(tmp_path) as index:
        node = next(node for node in index.watches.values() if node.inode == (config.stat().st_dev, config.stat().st_ino))
        captured = roster_events.Event(node.token, True, True)
        index.commit_dependencies("a", {})
        config.unlink()
        write(tmp_path, "a", ("new", 2))
        index.refresh_team("a")
        replacement = next(node for node in index.watches.values() if node.inode == (config.stat().st_dev, config.stat().st_ino))
        assert replacement.fd == node.fd
        assert replacement.token != node.token
        index.reconcile([captured])
        assert replacement.active
        assert_lookup(index, tmp_path, "old", "new")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux watch descriptor reuse")
def test_inotify_add_drains_retired_generation_before_reusing_watch_descriptor(tmp_path, monkeypatch):
    config = write(tmp_path, "a", ("desk", 1))
    events = roster_events.Inotify()
    fd = os.open(config, os.O_RDONLY)
    old = events.add(fd, str(config))
    wd = events.watches[old]
    original_capture = events.capture
    captures = 0

    def capture():
        nonlocal captures
        captures += 1
        if captures == 2:
            events.pending.append(roster_events.Event(old, True, True))
            del events.tokens[wd]
        else:
            original_capture()

    monkeypatch.setattr(events, "capture", capture)
    monkeypatch.setattr(events.libc, "inotify_add_watch", lambda *args: wd)
    try:
        new = events.add(fd, str(config))
        assert events.tokens[wd] == new != old
        assert events.cut() == [roster_events.Event(old, True, True)]
    finally:
        os.close(fd)
        events.close()


def test_terminal_discovery_created_before_watch_installation_is_not_suppressed(tmp_path, monkeypatch):
    team = tmp_path / "teams" / "a"
    team.mkdir(parents=True)
    config = team / "config.json"
    original = rosters.RosterIndex.open
    fired = False

    def open(self, path, owner, dependencies):
        nonlocal fired
        if owner == "a" and not fired:
            fired = True
            config.symlink_to("config.json")
        return original(self, path, owner, dependencies)

    monkeypatch.setattr(rosters.RosterIndex, "open", open)
    with rosters.RosterIndex(tmp_path) as index:
        for _ in range(2):
            with pytest.raises(OSError) as expected:
                reference(tmp_path, "desk")
            with pytest.raises(OSError) as actual:
                index.lookup("desk")
            assert actual.value.errno == expected.value.errno == errno.ELOOP


@pytest.mark.parametrize("failure", [errno.ENOSPC, errno.EMFILE, errno.EACCES])
def test_missing_config_watch_registration_failure_invalidates_generation(tmp_path, monkeypatch, failure):
    with rosters.RosterIndex(tmp_path) as index:
        team = tmp_path / "teams" / "a"
        team.mkdir(parents=True)
        original = index.events.add

        def add(fd, path):
            if Path(path) == team:
                raise OSError(failure, os.strerror(failure), path)
            return original(fd, path)

        monkeypatch.setattr(index.events, "add", add)
        with pytest.raises(roster_events.WatchLost, match="registration failed"):
            index.lookup("desk")
        write(tmp_path, "a", ("desk", 1))
        with pytest.raises(roster_events.WatchLost):
            index.lookup("desk")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux named child dispatch")
def test_unrelated_team_child_attribute_never_iterates_team_edges(tmp_path):
    for number in range(64):
        write(tmp_path, str(number), ("desk", number))
    sibling = tmp_path / "teams" / "unrelated-file"
    sibling.write_text("unrelated")
    with rosters.RosterIndex(tmp_path) as index:
        index.lookup("desk")
        before = dict(index.work)
        value = (tmp_path / "teams").stat()
        node = index.inodes[value.st_dev, value.st_ino]

        class NamedEdgesOnly(dict):
            def items(self):
                raise AssertionError("named event iterated the whole roster edge set")

        node.edges = NamedEdgesOnly(node.edges)
        sibling.chmod(0o600)
        assert index.lookup("desk") == reference(tmp_path, "desk")
        assert dict(index.work) == before


@pytest.mark.parametrize("symlink_parent", [False, True])
def test_relative_config_root_preserves_returned_path_representation(helper, monkeypatch, symlink_parent):
    monkeypatch.chdir(helper.parent)
    config = Path(helper.name)
    if symlink_parent:
        actual = helper / "actual"
        (actual / "child").mkdir(parents=True)
        (helper / "alias").symlink_to(actual / "child", target_is_directory=True)
        config = config / "alias" / ".." / "config"
    write(config, "a", ("desk", 1))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    try:
        assert roster_service.mailbox_of("desk") == reference(config, "desk")
    finally:
        roster_service.retire(config.absolute())


@pytest.mark.parametrize("frame", [None, [], "lookup", 1])
def test_non_object_request_does_not_terminate_owner(helper, frame):
    write(helper, "a", ("desk", 1))
    assert roster_service.mailbox_of("desk")
    directory = roster_service.runtime(helper)
    with roster_service.connect(directory) as connection:
        old = roster_service.handshake(connection, helper)
        connection.sendall(json.dumps(frame).encode() + b"\n")
    with roster_service.connect(directory) as connection:
        new = roster_service.handshake(connection, helper)
    assert new["pid"] == old["pid"]
    assert roster_service.mailbox_of("desk") == reference(helper, "desk")
