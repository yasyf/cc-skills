from __future__ import annotations

import argparse
import ast
import hashlib
import json
import multiprocessing
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from long_running import rulings


LEGACY_PR_DIFF_SOURCE = '''def pr_diff(args: argparse.Namespace, shell: ledger.Shell, repo: str, base: str, head: str) -> Diff:
    checkout = (entry_of(args) or {}).get("checkout")
    if not checkout or ledger.origin_repo(shell, Path(checkout)).lower() != repo.lower():
        return compare(shell, repo, base, head)
    git = ["git", "-C", checkout]
    if not FULL_SHA.fullmatch(head):
        head = shell.run(ghread.read("api", f"repos/{repo}/commits/{head}", "--jq", ".sha")).strip()
    tip = base if FULL_SHA.fullmatch(base) else shell.run([*git, "ls-remote", "--exit-code", "origin", base if base.startswith("refs/") else f"refs/heads/{base}"]).split()[0]
    if absent := missing_commits(shell, git, (tip, head)):
        shell.run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *absent])
    kept = state_dir(args, args.runtime.drives, args.runtime.scratch) / "check-pr" / f"{shell.run([*git, 'merge-base', tip, head]).strip()}-{head}.diff"
    if not kept.is_file():
        kept.parent.mkdir(parents=True, exist_ok=True)
        staged = kept.with_suffix(f".{os.getpid()}")
        shell.run([*git, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--find-copies-harder", "-l0", f"-U{LOOKBACK}", "--src-prefix=a/", "--dst-prefix=b/", f"--output={staged}", f"{tip}...{head}"])
        staged.replace(kept)
    return parse_diff(kept.read_text(errors="replace"))
'''


def git(checkout: Path, *arguments: str, stdin: str | None = None) -> str:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ["git", "-C", str(checkout), "-c", "core.hooksPath=" + os.devnull, *arguments],
        input=stdin, capture_output=True, text=True, check=True, env=env,
    ).stdout


def repository(root: Path, size: int) -> tuple[Path, str, str]:
    checkout = root / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "--object-format=sha1", "-b", "main")
    git(checkout, "config", "user.name", "Fixture")
    git(checkout, "config", "user.email", "fixture@example.invalid")
    git(checkout, "remote", "add", "origin", "https://github.com/acme/project.git")
    for index in range(size):
        (checkout / f"source-{index}.py").write_text("".join(f"source_{index}_value_{line} = {line}\n" for line in range(30)))
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "base")
    base = git(checkout, "rev-parse", "HEAD").strip()
    copied = (checkout / "source-0.py").read_text().replace("source_0_value_15 = 15", "required_new_value = 15")
    (checkout / "copied.py").write_text(copied)
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "copy")
    return checkout, base, git(checkout, "rev-parse", "HEAD").strip()


def arguments(root: Path, checkout: Path) -> argparse.Namespace:
    drives = root / "drives"
    drives.mkdir(exist_ok=True)
    entry = drives / "fixture.json"
    if not entry.exists():
        entry.write_text(json.dumps({"checkout": str(checkout), "state_dir": str(root / "state")}))
    return argparse.Namespace(drive="fixture", program=None, runtime=SimpleNamespace(drives=drives, scratch=root / "scratch"))


class GitShell:
    def __init__(self, records: Path, entered=None, release=None) -> None:
        self.records = records
        self.entered = entered
        self.release = release

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        assert argv[0] == "git", argv
        if argv[3] == "diff":
            with (self.records / f"diff-{os.getpid()}.jsonl").open("a") as output:
                output.write(json.dumps(argv) + "\n")
            if self.entered is not None:
                self.entered.set()
                assert self.release.wait(20), "fixture did not release its producer"
        return git(Path(argv[2]), *argv[3:], stdin=stdin)


def cold_reader(root: str, checkout: str, base: str, head: str, missed, entered, release, output, legacy: bool = False) -> None:
    root_path = Path(root)
    args = arguments(root_path, Path(checkout))
    expected = root_path / "state" / "check-pr" / f"{base}-{head}.diff"
    original = Path.is_file

    def observed(path: Path) -> bool:
        result = original(path)
        if path == expected and not result:
            missed.set()
        return result

    Path.is_file = observed
    try:
        selected = rulings.pr_diff
        if legacy:
            scope = vars(rulings).copy()
            exec(compile(LEGACY_PR_DIFF_SOURCE, "immutable811-pr-diff", "exec"), scope)
            selected = scope["pr_diff"]
        found = selected(args, GitShell(root_path, entered, release), "acme/project", base, head)
        output.put({"status": "PASS", "head": head, "source_sha256": hashlib.sha256(Path(rulings.__file__).read_bytes()).hexdigest(), "diff": asdict(found)})
    except BaseException as error:
        output.put({"status": "ERROR", "error": repr(error)})
    finally:
        Path.is_file = original


def diff_commands(root: Path) -> list[list[str]]:
    return [json.loads(line) for path in root.glob("diff-*.jsonl") for line in path.read_text().splitlines()]


def readers(root: Path, checkout: Path, base: str, head: str, legacy: bool = False) -> list[dict]:
    arguments(root, checkout)
    ctx = multiprocessing.get_context("spawn")
    missed = [ctx.Event(), ctx.Event()]
    entered, second_entered, release, output = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Queue()
    children = [
        ctx.Process(target=cold_reader, args=(str(root), str(checkout), base, head, missed[index], entered if index == 0 else second_entered if legacy else None, release, output, legacy and index == 0))
        for index in range(2)
    ]
    children[0].start()
    try:
        assert entered.wait(20), "first producer did not enter real Git diff"
        children[1].start()
        assert missed[1].wait(20), "second caller did not observe the real cold cache"
        if legacy:
            assert second_entered.wait(20), "current caller did not enter beside the unlocked immutable writer"
    finally:
        release.set()
        started = [child for child in children if child.pid is not None]
        try:
            results = [output.get(timeout=30) for _ in started]
        finally:
            for child in started:
                child.join(30)
            output.close()
            output.join_thread()
    assert [child.exitcode for child in children] == [0, 0]
    return results


@pytest.mark.parametrize("size", [32, 4096])
def test_same_cold_key_computes_one_copy_aware_diff(tmp_path: Path, size: int) -> None:
    checkout, base, head = repository(tmp_path, size)
    results = readers(tmp_path, checkout, base, head)
    commands = diff_commands(tmp_path)
    assert all(row["status"] == "PASS" for row in results), results
    assert {row["source_sha256"] for row in results if row["status"] == "PASS"} == {hashlib.sha256(Path(rulings.__file__).read_bytes()).hexdigest()}
    expected = git(checkout, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--find-copies-harder", "-l0", f"-U{rulings.LOOKBACK}", "--src-prefix=a/", "--dst-prefix=b/", f"{base}...{head}")
    assert "copy from source-0.py" in expected
    wanted = asdict(rulings.parse_diff(expected))
    assert results[0]["diff"] == results[1]["diff"] == wanted
    assert wanted["added"] == [("copied.py", 16, "required_new_value = 15")]
    assert wanted["removed"] == []
    assert any(row[0] == "copied.py" for row in wanted["kept_after"])
    assert all(flag in commands[0] for flag in ("--find-copies-harder", "-l0", "--no-ext-diff", "--no-textconv"))
    assert len(commands) == 1, f"duplicate expensive copy-aware diffs: expected 1, got {len(commands)}"
    args = arguments(tmp_path, checkout)
    assert asdict(rulings.pr_diff(args, GitShell(tmp_path), "acme/project", base, head)) == asdict(rulings.parse_diff(expected))
    assert len(diff_commands(tmp_path)) == 1
    print(json.dumps({"candidate_sources": size, "concurrent_cold_callers": 2, "expensive_diff_commands": len(commands), "diff_bytes": len(expected.encode()), "diff_sha256": hashlib.sha256(expected.encode()).hexdigest(), "copy_parse_parity": True, "warm_additional_diff_commands": 0}), flush=True)


@pytest.mark.parametrize("failure", [RuntimeError("diff failed"), KeyboardInterrupt("fixture interruption"), subprocess.CalledProcessError(42, ["git", "diff"], stderr="fixture diff error")])
def test_failed_producer_keeps_its_error_and_next_independent_call_can_fill(tmp_path: Path, failure: BaseException) -> None:
    checkout, base, head = repository(tmp_path, 2)
    args = arguments(tmp_path, checkout)
    kept = tmp_path / "state" / "check-pr" / f"{base}-{head}.diff"

    class FailingGit(GitShell):
        def run(self, argv: list[str], stdin: str | None = None) -> str:
            if argv[3] == "diff":
                Path(next(arg.removeprefix("--output=") for arg in argv if arg.startswith("--output="))).write_text("partial")
                raise failure
            return super().run(argv, stdin)

    with pytest.raises(type(failure)) as caught:
        rulings.pr_diff(args, FailingGit(tmp_path), "acme/project", base, head)
    assert caught.value is failure
    assert not kept.exists()
    found = rulings.pr_diff(args, GitShell(tmp_path), "acme/project", base, head)
    assert found.added == [("copied.py", 16, "required_new_value = 15")]
    assert len(diff_commands(tmp_path)) == 1


def test_same_process_cold_callers_do_not_race_the_pid_staged_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout, base, head = repository(tmp_path, 2)
    args = arguments(tmp_path, checkout)
    kept = tmp_path / "state" / "check-pr" / f"{base}-{head}.diff"
    entered, release, missed = threading.Event(), threading.Event(), threading.Event()
    original = Path.is_file

    def observed(path: Path) -> bool:
        result = original(path)
        if path == kept and not result and threading.current_thread().name == "second-caller":
            missed.set()
        return result

    monkeypatch.setattr(Path, "is_file", observed)

    def call(second: bool):
        if second:
            threading.current_thread().name = "second-caller"
        return rulings.pr_diff(args, GitShell(tmp_path, None if second else entered, release), "acme/project", base, head)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, False)
        try:
            assert entered.wait(20)
            second = pool.submit(call, True)
            assert missed.wait(20)
        finally:
            release.set()
        assert asdict(first.result(timeout=30)) == asdict(second.result(timeout=30))
    assert len(diff_commands(tmp_path)) == 1


def test_different_cold_keys_can_compute_concurrently(tmp_path: Path) -> None:
    checkout, base, first_head = repository(tmp_path, 2)
    (checkout / "another.py").write_text("another_required_value = 1\n")
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "next")
    heads = [first_head, git(checkout, "rev-parse", "HEAD").strip()]
    arguments(tmp_path, checkout)
    ctx = multiprocessing.get_context("spawn")
    entered, missed = [ctx.Event(), ctx.Event()], [ctx.Event(), ctx.Event()]
    release, output = ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=cold_reader, args=(str(tmp_path), str(checkout), base, heads[index], missed[index], entered[index], release, output)) for index in range(2)]
    for child in children:
        child.start()
    try:
        both_entered = all(event.wait(20) for event in entered)
    finally:
        release.set()
        try:
            results = [output.get(timeout=30) for _ in children]
        finally:
            for child in children:
                child.join(30)
            output.close()
            output.join_thread()
    assert [child.exitcode for child in children] == [0, 0]
    assert both_entered, "distinct immutable diff keys were serialized together"
    assert all(row["status"] == "PASS" for row in results), results
    assert len(diff_commands(tmp_path)) == 2


def test_warm_cache_does_not_request_writable_cache_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout, base, head = repository(tmp_path, 2)
    args = arguments(tmp_path, checkout)
    kept = tmp_path / "state" / "check-pr" / f"{base}-{head}.diff"
    kept.parent.mkdir(parents=True)
    expected = git(checkout, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--find-copies-harder", "-l0", f"-U{rulings.LOOKBACK}", "--src-prefix=a/", "--dst-prefix=b/", f"{base}...{head}")
    kept.write_text(expected)
    original = Path.open

    def readonly(path: Path, mode="r", *values, **options):
        assert path.parent != kept.parent or not any(value in mode for value in "wax+"), "warm cache requested a writer or lock"
        return original(path, mode, *values, **options)

    monkeypatch.setattr(Path, "open", readonly)
    assert asdict(rulings.pr_diff(args, GitShell(tmp_path), "acme/project", base, head)) == asdict(rulings.parse_diff(expected))
    assert diff_commands(tmp_path) == []


def test_immutable811_writer_and_current_reader_publish_complete_equivalent_cache(tmp_path: Path) -> None:
    legacy = ast.parse(LEGACY_PR_DIFF_SOURCE).body[0]
    assert hashlib.sha256(ast.dump(legacy, include_attributes=False).encode()).hexdigest() == "f793de7a5651c7143dae4ca5a3564d7331627232a5c4e31434d77b4b0e6b9f5b"
    checkout, base, head = repository(tmp_path, 2)
    results = readers(tmp_path, checkout, base, head, legacy=True)
    assert len(diff_commands(tmp_path)) == 2
    assert all(row["status"] == "PASS" for row in results), results
    assert results[0]["diff"] == results[1]["diff"]
    args = arguments(tmp_path, checkout)
    assert asdict(rulings.pr_diff(args, GitShell(tmp_path), "acme/project", base, head)) == results[0]["diff"]
    assert len(diff_commands(tmp_path)) == 2
