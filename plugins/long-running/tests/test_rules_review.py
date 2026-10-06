from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import ledger
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "scripts" / "rules-review.py"
spec = importlib.util.spec_from_file_location("rules_review", SCRIPT)
rules_review = importlib.util.module_from_spec(spec)
sys.modules["rules_review"] = rules_review
spec.loader.exec_module(rules_review)

REPO = "Forge-AI/monorepo"
LEDGER = "1a2b3c4d"
LEDGER_RULING = "ec2881eeea7fb693a984b9bebadede555c540745"
GO_RULING = "d74f85d0000000000000000000000000000000aa"
HEAD = "a" * 40
NEXT_HEAD = "b" * 40
OTHER_HEAD = "c" * 40
def file_diff(path: str, body: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+{body}\n"


def deleted_diff(path: str, body: str) -> str:
    return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\nindex 1234567..0000000\n--- a/{path}\n+++ /dev/null\n@@ -1 +0,0 @@\n-{body}\n"


LEDGER_FINDING = {"ruling": "ec2881e", "cite": "go/ci/resolve.go:42", "sentence": "The resolver reads the release ledger to pick bases.", "quote": "the ledger only records and renders"}


class FakeShell(rules_review.Shell):

    def __init__(self, checkout: Path):
        self.checkout = checkout
        self.rows: dict[str, dict[str, str]] = {}
        self.answers = [
            {"id": LEDGER_RULING, "title": "Is the release ledger ever a source of truth?", "body": "No. The ledger only records and renders."},
            {"id": GO_RULING, "title": "Where does new CLI logic go?", "body": "Go under go/ci."},
        ]
        self.diffs: dict[str, str] = {}
        self.diff_errors: set[str] = set()
        self.questions: dict[str, str] = {}
        self.states: dict[str, str] = {}
        self.replies: dict[str, dict] = {}
        self.reviews: list[tuple[str, dict]] = []
        self.on_pr: dict[str, list[dict]] = {}
        self.post_errors = 0
        self.status_errors = 0
        self.statuses: dict[str, list[dict]] = {}
        self.calls: list[list[str]] = []

    def dispatch(self, run_dir, question):
        self.questions[run_dir.name] = question
        self.states[str(run_dir)] = "running"
        return f"REPLY_FILE: {run_dir}/codex-r-1\n"

    def finish(self, pr: str, head: str, attempt: int = 1, reply: dict | None = None, state: str = "completed") -> None:
        run_dir = self.checkout.parent / "state" / f"{pr}-{head[:12]}-{attempt}"
        self.states[str(run_dir)] = state
        if reply is not None:
            (run_dir / "codex-r-1").write_text(json.dumps(reply))

    def run(self, argv, stdin=None):
        self.calls.append(list(argv))
        if argv[:3] == ["ccn", "ledger", "show"]:
            return json.dumps({"rows": [{"key": key, "fields": dict(fields)} for key, fields in self.rows.items()]})
        if argv[:4] == ["ccn", "ledger", "row", "set"]:
            key = argv[argv.index("--key") + 1]
            fields = dict(argv[index + 1].split("=", 1) for index, value in enumerate(argv) if value == "--field")
            self.rows.setdefault(key, {}).update(fields)
            return ""
        if argv[:4] == ["ccn", "-R", str(self.checkout), "answer"]:
            return json.dumps(self.answers)
        if argv[:2] == ["gh", "api"] and "/compare/" in argv[2]:
            head = argv[2].rsplit("...", 1)[1]
            if head in self.diff_errors:
                raise subprocess.CalledProcessError(1, argv, "", "HTTP 406: diff too large")
            return self.diffs.get(head, file_diff("go/ci/resolve.go", f"ledger.Base() // {head[:4]}"))
        if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
            pr = argv[4].split("/")[4]
            return json.dumps([self.on_pr.get(pr, [])])
        if argv[:2] == ["gh", "api"] and "/statuses/" in argv[2]:
            if self.status_errors:
                self.status_errors -= 1
                raise subprocess.CalledProcessError(1, argv, "", "HTTP 502: bad gateway")
            self.statuses.setdefault(argv[2].rsplit("/", 1)[1], []).append(json.loads(stdin))
            return "{}"
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/reviews"):
            if self.post_errors:
                self.post_errors -= 1
                raise subprocess.CalledProcessError(1, argv, "", "HTTP 502: bad gateway")
            payload = json.loads(stdin)
            self.reviews.append((argv[2], payload))
            self.on_pr.setdefault(argv[2].split("/")[4], []).append(payload)
            return "{}"
        if argv[:2] == ["git", "check-attr"]:
            return ledger.Shell.once(argv, stdin)
        if argv[:2] == ["codex-ask", "--collect"]:
            state = self.states[argv[2]]
            return json.dumps({"lane": ".", "state": state, "reply_file": f"{argv[2]}/codex-r-1"}) + "\n"
        raise AssertionError(f"unexpected call {argv}")


@pytest.fixture(autouse=True)
def elsewhere(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def checkout(tmp_path) -> Path:
    path = tmp_path / "checkout"
    path.mkdir()
    (path / "AGENTS.md").write_text("# AGENTS.md\nWrite new CLI logic in Go under go/ci.\n")
    (path / ".gitattributes").write_text("api/testdata/llm-cassettes/*.json linguist-generated=true\nschema.gql linguist-generated\ngo/ci/*.go -linguist-generated\n")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def shell(checkout) -> FakeShell:
    fake = FakeShell(checkout)
    fake.rows = {
        "30312": {"lane": "resolver", "state": "open", "head": HEAD, "base": "dev"},
        "30313": {"lane": "resolver", "state": "open", "head": OTHER_HEAD, "base": "resolver/1"},
        "30200": {"lane": "old", "state": "landed", "head": "d" * 40, "base": "dev"},
    }
    return fake


def sweep(shell: FakeShell, *extra: str) -> int:
    state = shell.checkout.parent / "state"
    return rules_review.main(["sweep", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(shell.checkout), "--state", str(state), *extra], shell)


def blocked(shell: FakeShell) -> dict[str, bool]:
    return {pr: ledger.rules_blocked(shell.rows, pr) for pr in shell.rows if pr.isdigit() and ledger.is_open(shell.rows[pr])}


def test_each_open_head_gets_one_detached_review_carrying_rulings_ethos_and_its_diff(shell, capsys):
    assert sweep(shell) == 0
    assert sweep(shell) == 0

    assert sorted(shell.questions) == [f"30312-{HEAD[:12]}-1", f"30313-{OTHER_HEAD[:12]}-1"]
    question = shell.questions[f"30312-{HEAD[:12]}-1"]
    assert "## ec2881e Is the release ledger ever a source of truth?" in question
    assert "AGENTS.md:2: Write new CLI logic in Go under go/ci." in question
    assert f"ledger.Base() // {HEAD[:4]}" in question
    assert ["gh", "api", f"repos/{REPO}/compare/resolver/1...{OTHER_HEAD}", "-H", rules_review.DIFF_MEDIA] in shell.calls
    assert shell.rows[ledger.review_key("30312", HEAD)]["verdict"] == "pending"
    assert blocked(shell) == {"30312": True, "30313": True}
    assert capsys.readouterr().out.splitlines() == [f"REVIEWING #30312 {HEAD[:9]}", f"REVIEWING #30313 {OTHER_HEAD[:9]}"]


def test_parallel_caps_how_many_reviews_run_at_once(shell):
    sweep(shell, "--parallel", "1")
    assert list(shell.questions) == [f"30312-{HEAD[:12]}-1"]

    shell.finish("30312", HEAD, reply={"verdict": "clean", "findings": []})
    sweep(shell, "--parallel", "1")
    assert list(shell.questions) == [f"30312-{HEAD[:12]}-1", f"30313-{OTHER_HEAD[:12]}-1"]


def test_a_finding_blocks_the_head_and_posts_one_review_quoting_the_ruling(shell, capsys):
    sweep(shell)
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    shell.finish("30313", OTHER_HEAD, reply={"verdict": "clean", "findings": []})
    capsys.readouterr()

    sweep(shell)
    sweep(shell)

    review = shell.rows[ledger.review_key("30312", HEAD)]
    assert json.loads(review["findings"])[0]["ruling"] == LEDGER_RULING
    assert review["comment"] == "posted"
    assert blocked(shell) == {"30312": True, "30313": False}
    assert len(shell.reviews) == 1
    path, payload = shell.reviews[0]
    assert path == f"repos/{REPO}/pulls/30312/reviews"
    assert payload["commit_id"] == HEAD and payload["event"] == "COMMENT"
    assert "**ec2881e** at `go/ci/resolve.go:42`" in payload["body"]
    assert "> the ledger only records and renders" in payload["body"]
    assert payload["body"].endswith(f"<!-- rules-review {HEAD} -->")
    assert capsys.readouterr().out.splitlines() == [
        f"RULES #30312 {HEAD[:9]} ec2881e: The resolver reads the release ledger to pick bases.",
        f"CLEAN #30313 {OTHER_HEAD[:9]}",
    ]


def test_a_new_head_is_reviewed_again_and_the_same_findings_post_no_second_comment(shell):
    sweep(shell)
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    sweep(shell)

    shell.rows["30312"]["head"] = NEXT_HEAD
    sweep(shell)
    assert f"30312-{NEXT_HEAD[:12]}-1" in shell.questions
    assert blocked(shell)["30312"]

    shell.finish("30312", NEXT_HEAD, reply={"verdict": "findings", "findings": [{**LEDGER_FINDING, "cite": "go/ci/resolve.go:44"}]})
    sweep(shell)
    assert shell.rows[ledger.review_key("30312", NEXT_HEAD)]["comment"] == "skipped-repeat"
    assert len(shell.reviews) == 1

    third = "e" * 40
    shell.rows["30312"]["head"] = third
    sweep(shell)
    shell.finish("30312", third, reply={"verdict": "findings", "findings": [{**LEDGER_FINDING, "cite": "go/ci/plan.go:7"}]})
    sweep(shell)
    assert len(shell.reviews) == 2 and shell.reviews[1][1]["commit_id"] == third


def test_a_root_override_naming_every_ruling_unblocks_the_pr_on_every_head(shell, tmp_path, capsys):
    inbox = tmp_path / "root-inbox.md"
    inbox.write_text("R900 (9:40 PM PT) rules-override #30312 d74f85d :: unrelated ruling\n")
    sweep(shell, "--inbox", str(inbox))
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    sweep(shell, "--inbox", str(inbox))
    assert blocked(shell)["30312"]

    inbox.write_text(inbox.read_text() + "R901 (9:42 PM PT) rules-override #30312 ec2881e :: owner accepts the ledger read for this PR\n")
    capsys.readouterr()
    sweep(shell, "--inbox", str(inbox))
    assert not blocked(shell)["30312"]
    assert shell.rows[ledger.review_key("30312", HEAD)]["override"] == "R901"
    assert capsys.readouterr().out.splitlines() == [f"OVERRIDDEN #30312 {HEAD[:9]} by R901"]

    shell.rows["30312"]["head"] = NEXT_HEAD
    sweep(shell, "--inbox", str(inbox))
    shell.finish("30312", NEXT_HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    sweep(shell, "--inbox", str(inbox))
    assert not blocked(shell)["30312"]
    assert shell.rows[ledger.review_key("30312", NEXT_HEAD)]["comment"] == "skipped-overridden"


def test_a_review_that_dies_retries_once_then_lets_the_head_land(shell, capsys):
    sweep(shell)
    shell.finish("30312", HEAD, state="died")
    sweep(shell)
    assert f"30312-{HEAD[:12]}-2" in shell.questions

    shell.finish("30312", HEAD, attempt=2, state="died")
    sweep(shell)
    sweep(shell)
    assert sorted(name for name in shell.questions if name.startswith("30312")) == [f"30312-{HEAD[:12]}-1", f"30312-{HEAD[:12]}-2"]
    assert shell.rows[ledger.review_key("30312", HEAD)]["verdict"] == "error"
    assert not blocked(shell)["30312"]
    assert f"REVIEW-ERROR #30312 {HEAD[:9]} attempt 2: codex run died" in capsys.readouterr().out


def test_an_unreadable_diff_records_an_error_without_dispatching(shell, capsys):
    shell.diff_errors.add(HEAD)
    sweep(shell)

    assert f"30312-{HEAD[:12]}-1" not in shell.questions
    review = shell.rows[ledger.review_key("30312", HEAD)]
    assert review["verdict"] == "error" and review["error"] == "HTTP 406: diff too large"
    assert not blocked(shell)["30312"]
    assert f"REVIEW-ERROR #30312 {HEAD[:9]} attempt 1: HTTP 406: diff too large" in capsys.readouterr().out


def test_a_diff_over_the_bound_is_an_error_never_a_partial_review(shell, capsys):
    shell.diffs[HEAD] = file_diff("go/ci/resolve.go", "x" * rules_review.DIFF_CHARS)
    sweep(shell)

    assert f"30312-{HEAD[:12]}-1" not in shell.questions
    assert shell.rows[ledger.review_key("30312", HEAD)]["verdict"] == "error"
    assert f"over the {rules_review.DIFF_CHARS}-character review bound" in capsys.readouterr().out
    assert not blocked(shell)["30312"]


def test_a_reply_whose_verdict_contradicts_its_findings_is_an_error(shell):
    sweep(shell)
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": []})
    sweep(shell)

    assert shell.rows[ledger.review_key("30312", HEAD)]["error"] == "codex replied findings with 0 findings"
    assert f"30312-{HEAD[:12]}-2" in shell.questions


def status_states(shell: FakeShell, head: str) -> list[str]:
    return [status["state"] for status in shell.statuses.get(head, []) if status["context"] == rules_review.STATUS_CONTEXT]


def test_a_pending_review_blocks_the_head_until_its_verdict_lands(shell):
    sweep(shell)
    assert blocked(shell) == {"30312": True, "30313": True}
    assert status_states(shell, HEAD) == ["pending"]

    shell.finish("30313", OTHER_HEAD, reply={"verdict": "clean", "findings": []})
    sweep(shell)
    assert blocked(shell) == {"30312": True, "30313": False}
    assert status_states(shell, HEAD) == ["pending"]
    assert status_states(shell, OTHER_HEAD) == ["pending", "success"]

    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    sweep(shell)
    assert blocked(shell)["30312"]
    assert shell.rows[ledger.review_key("30312", HEAD)]["comment"] == "posted"


def test_an_agents_md_line_is_waived_only_by_its_exact_id(shell, tmp_path):
    ethos = {"ruling": "AGENTS.md:12", "cite": "go/ci/verb.ts:3", "sentence": "Adds a TypeScript verb.", "quote": "add no TypeScript verbs"}
    inbox = tmp_path / "root-inbox.md"
    inbox.write_text("R904 rules-override #30312 AGENTS.md:1 :: wrong line\n")
    sweep(shell, "--inbox", str(inbox))
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [ethos]})
    sweep(shell, "--inbox", str(inbox))
    assert blocked(shell)["30312"]

    inbox.write_text("R905 rules-override #30312 AGENTS.md:12 :: owner allows this verb\n")
    sweep(shell, "--inbox", str(inbox))
    assert not blocked(shell)["30312"]


def test_a_failed_post_retries_and_a_post_whose_ledger_write_was_lost_is_not_repeated(shell):
    sweep(shell)
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    shell.post_errors = 1
    sweep(shell)
    review = shell.rows[ledger.review_key("30312", HEAD)]
    assert "comment" not in review and review["comment_error"] == "HTTP 502: bad gateway"

    sweep(shell)
    assert len(shell.reviews) == 1 and shell.rows[ledger.review_key("30312", HEAD)]["comment"] == "posted"

    shell.rows[ledger.review_key("30312", HEAD)]["comment"] = ""
    sweep(shell)
    assert len(shell.reviews) == 1 and shell.rows[ledger.review_key("30312", HEAD)]["comment"] == "posted"


def test_the_sweep_reads_the_ledger_from_the_checkout_whatever_the_working_directory(shell):
    sweep(shell)
    assert Path.cwd() == shell.checkout.resolve()


def test_a_row_without_a_base_waits_for_its_first_refresh(shell):
    shell.rows["30313"].pop("base")
    sweep(shell)
    assert list(shell.questions) == [f"30312-{HEAD[:12]}-1"]


def test_paths_gitattributes_marks_generated_leave_the_reviewed_diff_and_its_bound(shell, capsys):
    cassette = "x" * rules_review.DIFF_CHARS
    shell.diffs[HEAD] = "".join([
        file_diff("api/testdata/llm-cassettes/b.json", cassette),
        file_diff("go/ci/resolve.go", "ledger.Base()"),
        file_diff("api/testdata/llm-cassettes/a.json", cassette),
        file_diff("schema.gql", "type Query"),
    ])
    sweep(shell)

    question = shell.questions[f"30312-{HEAD[:12]}-1"]
    assert "+ledger.Base()" in question
    assert "llm-cassettes" not in question and "schema.gql" not in question
    assert shell.rows[ledger.review_key("30312", HEAD)]["verdict"] == "pending"
    assert capsys.readouterr().out.splitlines()[:2] == [
        f"GENERATED #30312 {HEAD[:9]} 3 paths left out of the review: api/testdata/llm-cassettes/a.json api/testdata/llm-cassettes/b.json schema.gql",
        f"REVIEWING #30312 {HEAD[:9]}",
    ]


def test_the_commit_status_mirrors_each_verdict_once_and_an_override_turns_it_green(shell, tmp_path):
    inbox = tmp_path / "root-inbox.md"
    inbox.write_text("")
    sweep(shell, "--inbox", str(inbox))
    shell.finish("30312", HEAD, reply={"verdict": "findings", "findings": [LEDGER_FINDING]})
    sweep(shell, "--inbox", str(inbox))
    sweep(shell, "--inbox", str(inbox))
    assert status_states(shell, HEAD) == ["pending", "failure"]
    assert shell.statuses[HEAD][-1]["description"].startswith("rulings violated: ec2881e")

    inbox.write_text("R901 rules-override #30312 ec2881e :: owner accepts the ledger read for this PR\n")
    sweep(shell, "--inbox", str(inbox))
    assert status_states(shell, HEAD) == ["pending", "failure", "success"]
    assert shell.statuses[HEAD][-1]["description"] == "findings on ec2881e waived by R901"


def test_an_errored_review_posts_a_green_status_naming_the_error(shell):
    shell.diff_errors.add(HEAD)
    sweep(shell)

    assert status_states(shell, HEAD) == ["success"]
    assert shell.statuses[HEAD][-1]["description"] == "not reviewed, so not held: HTTP 406: diff too large"


def test_a_failed_status_post_is_recorded_and_retried(shell):
    shell.status_errors = 1
    sweep(shell)
    review = shell.rows[ledger.review_key("30312", HEAD)]
    assert "status" not in review and review["status_error"] == "HTTP 502: bad gateway"

    sweep(shell)
    assert status_states(shell, HEAD) == ["pending"]
    assert shell.rows[ledger.review_key("30312", HEAD)]["status"] == "pending"


def test_a_pure_deletion_passes_clean_without_a_codex_run(shell, capsys):
    shell.diffs[HEAD] = deleted_diff("go/ci/old.go", "x" * rules_review.DIFF_CHARS) + deleted_diff("api/src/legacy.ts", "export {}")
    sweep(shell)

    assert f"30312-{HEAD[:12]}-1" not in shell.questions
    review = shell.rows[ledger.review_key("30312", HEAD)]
    assert review["verdict"] == "clean" and review["note"] == "deletion only"
    assert not blocked(shell)["30312"]
    assert shell.statuses[HEAD][-1] == {"state": "success", "context": rules_review.STATUS_CONTEXT, "description": "deletion only: no added line to review"}
    assert capsys.readouterr().out.splitlines()[:2] == [
        f"DELETED #30312 {HEAD[:9]} 2 deleted files left out of the review: api/src/legacy.ts go/ci/old.go",
        f"CLEAN #30312 {HEAD[:9]} deletion only",
    ]


def test_the_review_reads_added_lines_with_their_context_numbered_by_the_new_tree(shell, capsys):
    path = "go/ci/resolve.go"
    hunk = [
        "@@ -10,12 +10,11 @@ func Resolve() {",
        " context1",
        " context2",
        " context3",
        " context4",
        "-removed1",
        "-removed2",
        "+added1",
        " context5",
        " context6",
        " context7",
        " context8",
        " context9",
        "+added2",
        "\\ No newline at end of file",
    ]
    modified = "\n".join([f"diff --git a/{path} b/{path}", "index 1111111..2222222 100644", f"--- a/{path}", f"+++ b/{path}", *hunk]) + "\n"
    shell.diffs[HEAD] = deleted_diff("go/ci/old.go", "x" * rules_review.DIFF_CHARS) + modified
    sweep(shell)

    question = shell.questions[f"30312-{HEAD[:12]}-1"]
    reviewed = question.split("```diff\n", 1)[1].split("\n```", 1)[0]
    assert reviewed.splitlines() == [
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
        "@@ +12,5 @@",
        " context3",
        " context4",
        "+added1",
        " context5",
        " context6",
        "@@ +18,3 @@",
        " context8",
        " context9",
        "+added2",
    ]
    assert "old.go" not in question
    assert capsys.readouterr().out.splitlines()[:2] == [
        f"DELETED #30312 {HEAD[:9]} 1 deleted files left out of the review: go/ci/old.go",
        f"REVIEWING #30312 {HEAD[:9]}",
    ]
