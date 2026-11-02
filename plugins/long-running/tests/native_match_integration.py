from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


HELPER_TEST = "-test.run=^TestNativeAnswerMatchFixtureProcess$"


def private_environment(home: Path) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(home),
        "XDG_CACHE_HOME": str(home / "cache"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CEILING_DIRECTORIES": str(home.parent),
        "CC_NOTES_ACTOR": "Native Match Fixture <fixture@example.com>",
        "LANG": "C.UTF-8",
    }


class NativeFixture:
    def __init__(self, binary: Path, repo: Path, home: Path, namespace: str = "control"):
        self.binary, self.repo, self.home, self.namespace = binary, repo, home, namespace
        self.home.mkdir(parents=True, exist_ok=True)
        self.serial = 0

    def config(self, operation: str, **values):
        self.serial += 1
        prefix = self.home / f"{self.namespace}-{operation}-{self.serial}"
        metrics = prefix.with_suffix(".metrics.json")
        path = prefix.with_suffix(".config.json")
        assert not metrics.exists()
        with path.open("x") as output:
            json.dump({"operation": operation, "repo": str(self.repo), "program": "brook", "metrics": str(metrics), **values}, output)
        path.chmod(0o600)
        return path, metrics

    def call(self, operation: str, **values):
        config, metrics = self.config(operation, **values)
        env = private_environment(self.home) | {"CCX_NATIVE_MATCH_FIXTURE_CONFIG": str(config)}
        child = subprocess.run([str(self.binary), HELPER_TEST], env=env, capture_output=True, text=True, check=True, timeout=1200)
        return json.loads(child.stdout), json.loads(metrics.read_text())


def driver(config: dict) -> dict:
    sys.path.insert(0, config["scripts"])
    from long_running import ledger, rulings

    fixture = NativeFixture(Path(config["binary"]), Path(config["repo"]), Path(config["home"]), config["namespace"])
    calls = {"answer_lists": 0, "answer_rows": 0, "answer_bytes": 0, "register": 0, "legacy_search": 0, "session": 0}
    mirror = {"reads": 0, "bytes": 0, "writes": 0}
    directory = fixture.home / ".claude" / "scratch" / config.get("program", "brook") / "rulings"
    original_read, original_write = Path.read_text, rulings.write_atomic

    def read(path, *args, **kwargs):
        text = original_read(path, *args, **kwargs)
        if path.parent == directory and path.suffix == ".md":
            mirror["reads"] += 1
            mirror["bytes"] += len(text.encode())
        return text

    def write(path, text):
        if path.parent == directory:
            mirror["writes"] += 1
        return original_write(path, text)
    session_metrics = None

    class Shell:
        def run(self, argv, stdin=None):
            if argv[0] == "git":
                return subprocess.check_output(argv, env=private_environment(fixture.home), text=True)
            if argv[:3] == ["ccn", "-R", config["repo"]]:
                if argv[3:5] == ["answer", "list"]:
                    calls["answer_lists"] += 1
                    labels = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--label"]
                    branch = argv[argv.index("--branch") + 1] if "--branch" in argv else ""
                    rows, _ = fixture.call("answers", labels=labels, branch=branch)
                    payload = json.dumps(rows)
                    calls["answer_rows"] += len(rows)
                    calls["answer_bytes"] += len(payload.encode())
                    return payload
                assert argv[3:5] == ["doc", "list"], argv
                calls["register"] += 1
                if config.get("register_error"):
                    raise RuntimeError("private register error")
                body = config.get("register_body", "")
                return json.dumps([{"id": "f" * 40, "title": "Register", "updated_at": "2026-10-01T00:00:00Z", "body": body}] if body else [])
            assert argv[:3] == ["ccx", "code", "search"], argv
            calls["legacy_search"] += 1
            rows, _ = fixture.call("search", mirror=argv[-1], query=argv[-2], k=int(argv[argv.index("-k") + 1]))
            return "\n".join(f"{row["file_path"]}:{row["start_line"]}-{row["end_line"]}" for row in rows)

        def session(self, argv, prepare, **kwargs):
            nonlocal session_metrics
            calls["session"] += 1
            options = {"mutation": config["during"]} if "during" in config else {}
            path, metrics = fixture.config("session", **options)
            kwargs["env"] = private_environment(fixture.home) | {"CCX_NATIVE_MATCH_FIXTURE_CONFIG": str(path)}
            try:
                return ledger.Shell.session([str(fixture.binary), HELPER_TEST, "--", *argv[1:]], prepare, **kwargs)
            finally:
                if metrics.exists():
                    session_metrics = json.loads(metrics.read_text())

    output = io.StringIO()
    result = {"calls": calls, "error": None, "mirror": mirror}
    Path.read_text, rulings.write_atomic = read, write
    try:
        args = ["match", "--repo", config["repo"], "--program", config.get("program", "brook"), "-k", str(config.get("k", 5)), "--budget", str(config.get("budget", 1000000))]
        for excluded in config.get("exclude", []):
            args += ["--exclude", excluded]
        assert rulings.main(args, Shell(), stdin=io.StringIO(config.get("query", "Owner words λ.")), stdout=output) == 0
    except BaseException as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        Path.read_text, rulings.write_atomic = original_read, original_write
    result.update(words=output.getvalue(), metrics=session_metrics)
    return result


class Corpus:
    def __init__(self, root: Path, count: int):
        self.root = root
        self.repo = root / "repo"
        self.repo.mkdir()
        self.binary = Path(os.environ["CCX_NATIVE_MATCH_TEST_BINARY"]).resolve(strict=True)
        self.baseline = Path(os.environ["LR_MATCH_BASELINE_SCRIPTS"]).resolve(strict=True)
        self.current = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "scripts"
        self.fixture = NativeFixture(self.binary, self.repo, root / "control")
        env = private_environment(self.fixture.home)
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(self.repo)], env=env, check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--allow-empty", "-qm", "fixture"], env=env, check=True)
        self.answers, self.setup = self.fixture.call("seed", count=count, branch="main")
        assert len(self.answers) == count
        assert self.setup["setup_git_calls"] == 3 * count + 1
        assert self.setup["setup_canonical_reads"] == count
        self.setup["short_id_members"] = len({row["id"][:7] for row in self.answers})
        self.serial = 0

    def run(self, mode, **values):
        self.serial += 1
        home = self.root / mode
        home.mkdir(exist_ok=True)
        config = self.root / f"{mode}-{self.serial}.json"
        config.write_text(json.dumps({"scripts": str(self.baseline if mode == "legacy" else self.current), "binary": str(self.binary), "repo": str(self.repo), "home": str(home), "namespace": config.stem, **values}))
        env = private_environment(home)
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--driver", str(config)], cwd=self.root, env=env, capture_output=True, text=True, check=True, timeout=1200)
        return json.loads(result.stdout)

    def parity(self, **values):
        legacy, current = self.run("legacy", **values), self.run("current", **values)
        assert legacy["error"] is None and current["error"] is None, (legacy, current)
        assert legacy["words"] == current["words"]
        assert current["calls"]["answer_lists"] == 0
        return legacy, current

    def mutate(self, **mutation):
        return self.fixture.call("mutate", mutation=mutation)[0]


@pytest.mark.parametrize("count", [32, 10000])
def test_actual_match_warm_body_work_and_immutable818_output(tmp_path, count):
    corpus = Corpus(tmp_path, count)
    mode = os.environ["LR_MATCH_COST_DRIVER"]
    assert mode in {"legacy", "current"}
    cold, warm = corpus.run(mode), corpus.run(mode)
    assert cold["error"] is None and warm["error"] is None, (cold, warm)
    assert cold["words"] and warm["words"], (cold, warm)
    print("NATIVE_PYTHON_MATCH_WORK " + json.dumps({"count": count, "driver": mode, "setup": corpus.setup, "cold": cold, "warm": warm}, sort_keys=True))
    assert warm["calls"]["answer_rows"] == warm["mirror"]["reads"] == 0, "unchanged match still materializes answer or mirror bodies"
    metrics = warm["metrics"]
    assert cold["metrics"]["text_calls"] == cold["metrics"]["source_records"]
    assert metrics["source_records"] > 0
    assert metrics["text_calls"] == metrics["text_bytes"] == 0
    assert metrics["encode_texts_including_query"] == 1
    assert metrics["ready_frames"] == metrics["result_frames"] == metrics["search_calls"] == 1
    assert len(metrics["pick_request_ids"]) == len(metrics["result_ids"]) <= 5
    legacy = corpus.run("legacy")
    assert legacy["error"] is None and legacy["words"] == warm["words"]
    assert legacy["calls"]["answer_lists"] == 2


@pytest.mark.parametrize("case", ["body", "title", "metadata", "unicode", "delete_restore", "branch", "cited", "excluded", "budget", "empty_program", "empty_body", "query_cut", "extra_markdown", "cache_corrupt", "register_error", "source_error", "held_writer"])
def test_actual_match_source_policy_and_snapshot_parity(tmp_path, case):
    corpus = Corpus(tmp_path, 32)
    _, before = corpus.parity(k=32)
    assert before["words"]
    identity = before["metrics"]["result_ids"][0]
    options = {"k": 32}
    changed = None
    if case in {"body", "title", "unicode", "empty_body", "metadata"}:
        changes = {"body": {"body": "Fresh owner words."}, "title": {"title": "Fresh title"}, "unicode": {"body": "漢字 λ 🐳\nSecond line."}, "empty_body": {"body": ""}, "metadata": {"add_tags": ["fixture:metadata"]}}[case]
        changed = corpus.mutate(action="edit", id=identity, **changes)
    elif case == "delete_restore":
        removed = corpus.mutate(action="delete_ref", id=identity)
        _, deleted = corpus.parity(**options)
        assert identity not in deleted["metrics"]["result_ids"] and deleted["metrics"]["text_calls"] == 0
        corpus.mutate(action="restore_ref", id=identity, tip=removed["head"])
    elif case == "branch":
        created, _ = corpus.fixture.call("create", specs=[{"Title": "Branch owner", "Body": "Other branch words.", "Tags": ["scope:durable"], "Anchors": {"Branches": ["other"]}}])
        subprocess.run(["git", "-C", str(corpus.repo), "checkout", "-qb", "other"], env=private_environment(corpus.fixture.home), check=True)
        options["k"] = 33
        branch_id = created[0]["id"]
    elif case == "cited":
        _, five = corpus.parity(k=5)
        identity = five["metrics"]["result_ids"][0]
        options.update(k=5, register_body=f"Owner words ({identity[:7]}).")
    elif case == "excluded":
        _, five = corpus.parity(k=5)
        identity = five["metrics"]["result_ids"][0]
        corpus.parity(k=5, exclude=[f" {identity[:7]} ", identity[:7] + "-extra", identity])
        options.update(k=5, exclude=[identity[:7]])
    elif case == "budget":
        options["budget"] = 1
    elif case == "empty_program":
        subprocess.run(["git", "-C", str(corpus.repo), "checkout", "-q", "--detach"], env=private_environment(corpus.fixture.home), check=True)
        options["program"] = "no-answers"
    elif case == "query_cut":
        options["query"] = "λ" * 3000
    elif case == "extra_markdown":
        extra = corpus.root / "current" / ".claude" / "scratch" / "brook" / "rulings" / "dead000.md"
        extra.write_text("Not canonical owner input.")
    elif case == "cache_corrupt":
        paths = list((corpus.root / "current" / ".claude" / "scratch" / "brook" / "rulings" / "ccx").glob("*/index.sqlite"))
        assert len(paths) == 1
        paths[0].write_bytes(b"invalid owned derived cache")
        failed = corpus.run("current", **options)
        assert failed["error"]["type"] == "CalledProcessError"
        assert failed["metrics"]["text_calls"] == 0
        assert corpus.run("legacy", **options)["words"] == before["words"]
        return
    elif case == "register_error":
        old, current = corpus.run("legacy", register_error=True), corpus.run("current", register_error=True)
        assert old["error"]["type"] == current["error"]["type"] == "RuntimeError"
        assert current["metrics"]["ready_frames"] == 1
        assert current["metrics"]["search_calls"] == current["metrics"]["result_frames"] == 0
        return
    elif case == "source_error":
        source = corpus.repo / ".git"
        source.rename(corpus.repo / "not-git")
        old, current = corpus.run("legacy"), corpus.run("current")
        assert old["error"]["type"] == current["error"]["type"] == "CalledProcessError"
        assert old["calls"]["register"] == current["calls"]["register"] == 0
        return
    elif case == "held_writer":
        held = corpus.run("current", k=32, during={"id": identity, "action": "edit", "timing": "after_rank", "body": "Words after held cut."})
        assert held["error"] is None and held["words"] == before["words"]
        _, fresh = corpus.parity(k=32)
        assert fresh["metrics"]["text_calls"] == 1
        assert "Words after held cut." in fresh["words"]
        return
    _, current = corpus.parity(**options)
    metrics = current["metrics"]
    if case in {"body", "title", "unicode", "empty_body"}:
        assert metrics["text_calls"] == 1
        assert changed["title"] in current["words"]
        if changed["body"]:
            assert changed["body"] in current["words"]
    if case in {"metadata", "delete_restore", "extra_markdown"}:
        assert metrics["text_calls"] == 0
    if case in {"cited", "excluded"}:
        assert len(metrics["result_ids"]) == 4
        assert identity not in metrics["result_ids"]
    if case == "branch":
        assert branch_id in metrics["result_ids"]
        assert "Other branch words." in current["words"]
    if case == "query_cut":
        assert metrics["query"] == "λ" * 2000
    if case == "empty_program":
        assert metrics["source_records"] == metrics["search_calls"] == 0
    if case == "budget":
        assert current["words"] == ""


if __name__ == "__main__":
    assert sys.argv[1] == "--driver"
    print(json.dumps(driver(json.loads(Path(sys.argv[2]).read_text())), ensure_ascii=False))
