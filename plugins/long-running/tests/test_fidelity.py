"""fidelity.py against a fake gh, git, rulings.py, codex-ask and cci: when a head carries an earlier grade with no model call, and the lines a codex grade posts."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import fidelity
import finder
import pytest
from conftest import gateway
from long_running.grades import OLD_APP, PORT_PHRASE, TEST_ONLY_PHRASE, carries, graded_head, owner_grounded

REPO = "Forge-AI/monorepo"
HEAD = "7414944e30bfec89dee45328414dbc1016e35afa"
OLD = "a269784e49009c5aa92b2341e08d6945f7ff8e95"
BASE = "d011466aa1b2c3d4e5f60718293a4b5c6d7e8f90"
OWN = "e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4"
MOVED = "8a6c4e2f0b9d7a5c3e1f8b6d4a2c0e9f7b5d3a1c"
OLD_OWN = "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d"
KEYS = ["rule/949e6be", "prompt/1026-105052"]
ROWS = "".join(f"### {key} | scope | in-force | said 1 | 2026-10-22\n> the owner's words\n\n" for key in KEYS) + "slice-hash: 0123456789ab\n"
DIFF = "diff --git a/go/sand/next/replica/follow.go b/go/sand/next/replica/follow.go\n--- a/go/sand/next/replica/follow.go\n+++ b/go/sand/next/replica/follow.go\n@@ -40,0 +41,3 @@\n+resubscribe()\n"
SWEPT = "The resubscribe hunk keeps the fetched position across a cut and every caller passes it through, so the streaming row holds."
BASELINE = "c57cad26 (release 10435, fleet sand 0.162.1876)"
DESK_OK = {"seq": 170001, "lane": "fidelity-desk-5", "text": f"fidelity: ok {OLD} {PORT_PHRASE}: ports old-tree 304c1e7ea5e (#41725) line for line."}
DESK_OLD_APP = {"seq": 170002, "lane": "fidelity-desk-5", "head": OLD[:7], "line": f"old-app: unchanged {OLD[:7]} baseline {BASELINE}; go/sand/next only"}
SAME = {(f"{OWN}^", HEAD): "5f1e", (f"{OLD}~1", OLD): "5f1e"}


class Fake:
    def __init__(self, tmp_path: Path, replies: list[dict] = (), patches: dict | None = None, state: str = "OPEN", heads: tuple[str, ...] = (HEAD,), known: tuple[str, ...] = (OLD,)):
        self.tmp_path = tmp_path
        self.known = known
        self.replies = list(replies)
        self.patches = SAME if patches is None else patches
        self.state = state
        self.heads = list(heads)
        self.calls: list[list[str]] = []
        self.range_diff = f"1:  {OLD_OWN[:7]} ! 1:  {OWN[:7]} resubscribe\n"
        self.shows = {OLD_OWN[:7]: "diff --git a/f.go b/f.go\n+old\n", OWN[:7]: "diff --git a/f.go b/f.go\n+new\n"}
        self.posted: list[dict] = []

    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = finder.READ_SECONDS) -> str:
        argv = gateway(argv)
        self.calls.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return json.dumps({"state": self.state, "baseRefName": "dev", "headRefOid": head, "body": "## Summary\nA cut signal resubscribes from the position it fetched.", "comments": [{"body": "### Rulings\n- ports old-tree 304c1e7ea5e (#41725)"}, {"body": "Body addendum\nThe caller trace for GetIrisTeamCreds runs through serve only."}, {"body": "ci is green"}]})
        if argv[0] == "git":
            return self.git(argv[1:])
        if argv[:2] == ["sh", "-c"]:
            return f"{self.patches[(argv[-2], argv[-1])]} {argv[-1]}\n" if (argv[-2], argv[-1]) in self.patches else ""
        if argv[:2] == [sys.executable, str(finder.SCRIPTS / "rulings.py")]:
            return ROWS if argv[2] == "slice" else "/plans/durable/plan/plan.md\n"
        if argv[0] == "codex-ask":
            reply = self.tmp_path / f"reply-{len(self.calls)}.json"
            reply.write_text(json.dumps(self.replies.pop(0)))
            return f"REPLY_FILE: {reply}\n"
        if argv[:2] == ["cci", "post"]:
            flags = [arg for arg in argv[2:] if arg != "--force"]
            self.posted.append(dict(zip(flags[::2], flags[1::2], strict=True)))
            return f"#{150001 + len(self.posted)}\n"
        raise AssertionError(f"unexpected call {argv}")

    def git(self, argv: list[str]) -> str:
        if argv[0] == "ls-remote":
            return "".join(f"{BASE}\t{ref}\n" for ref in argv[2:])
        if argv[0] in ("fetch", "merge-base"):
            return ""
        if argv[0] == "rev-list":
            assert argv[-1] in (f"{BASE}...{HEAD}", f"{BASE}...{OLD}")
            return f"{OWN}\n" if argv[-1].endswith(HEAD) else f"{OLD_OWN}\n"
        if argv[0] == "range-diff":
            return self.range_diff
        if argv[0] == "show":
            return self.shows.get(argv[-1], "")
        if argv[0] == "rev-parse":
            named = argv[-1].removesuffix("^{commit}")
            if (full := next((sha for sha in self.known if named in (sha, sha[:7])), None)) is None:
                raise subprocess.CalledProcessError(1, argv)
            return f"{full}\n"
        if argv[0] == "diff":
            return DIFF
        raise AssertionError(f"unexpected git {argv}")

    def asks(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[0] == "codex-ask"]

    def texts(self) -> list[str]:
        return [post["--text"] for post in self.posted]


def job(tmp_path: Path, **changes) -> Path:
    directory = tmp_path / fidelity.LANE / f"43299-{HEAD[:12]}-149000"
    directory.mkdir(parents=True)
    spec = {
        "drive": "d1",
        "repo": REPO,
        "pr": "43299",
        "head": HEAD,
        "lane": "p8a-step9",
        "to": ["desk-runner", "board-refresher-2"],
        "readers": ["fidelity-desk-4", "fidelity-desk-5"],
        "lanes": ["fidelity-desk-4", "fidelity-desk-5", fidelity.LANE],
        "grades": [DESK_OK],
        "old_apps": [DESK_OLD_APP],
        "corrected": [],
        "settled": {},
        "contract": "contract: builds the resubscribe from the fetched position; deletes nothing",
        "exempt": {"port": PORT_PHRASE, "test-only": TEST_ONLY_PHRASE},
        "exempt_grades": [PORT_PHRASE, TEST_ONLY_PHRASE],
        "baseline": BASELINE,
        "checkout": str(tmp_path / "checkout"),
        "model": "sol",
        "fast": True,
    }
    (directory / fidelity.JOB).write_text(json.dumps(spec | changes))
    return directory


def reply(verdict: str = "ok", missing: tuple[dict, ...] = (), checked: list[str] = KEYS, exempt: str = "none", reader: str = "", touches: tuple[str, ...] = (), old_app: str = "unchanged") -> dict:
    return {
        "verdict": verdict,
        "summary": "Resubscribes from the fetched position after a cut.",
        "swept": SWEPT,
        "checked": checked,
        "names": ["poll", "resubscribe"],
        "missing": list(missing),
        "touches": list(touches),
        "exempt": exempt,
        "reader": reader,
        "old_app": {"grade": old_app, "why": "go/sand/next only"},
    }


def result(directory: Path) -> dict:
    return json.loads((directory / fidelity.RESULT).read_text())


def test_a_restack_patch_identical_to_a_graded_ok_head_carries_its_port_phrase_and_old_app_line_with_no_model_call(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path)
    assert fidelity.main([str(directory)], shell) == 0
    assert not shell.asks()
    grade, old_app = shell.texts()
    assert grade == f"fidelity: ok {HEAD} {PORT_PHRASE}: carried from {OLD[:7]} (cci #170001), patch-identical by git patch-id over its own commits."
    assert old_app == f"old-app: unchanged {HEAD[:7]} baseline {BASELINE}; go/sand/next only"
    assert carries(grade) and graded_head(grade) == HEAD and OLD_APP.search(old_app)[1] == "unchanged"
    assert all(post["--lane"] == fidelity.LANE and post["--to"] == "p8a-step9,desk-runner,board-refresher-2" for post in shell.posted)
    assert result(directory) == {"verdict": "carried", "from": OLD, "posted": ["#150002", "#150003"]}


def test_every_earlier_graded_head_is_fetched_and_patch_read_at_once_and_the_newest_match_carries(tmp_path):
    newer = {"seq": 170005, "lane": "fidelity-desk-5", "text": f"fidelity: ok {MOVED} an earlier restack that reads the same."}
    read = {OLD: threading.Event(), MOVED: threading.Event()}
    overlapped = []

    class Slow(Fake):
        def run(self, argv: list[str], cwd: Path | None = None, timeout: int = finder.READ_SECONDS) -> str:
            if argv[:2] == ["sh", "-c"] and argv[-1] in read:
                read[argv[-1]].set()
                overlapped.append(all(event.wait(5) for event in read.values()))
            return super().run(argv, cwd, timeout)

    directory = job(tmp_path, grades=[DESK_OK, newer])
    shell = Slow(tmp_path, known=(OLD, MOVED))
    assert fidelity.main([str(directory)], shell) == 0
    assert overlapped == [True, True] and not shell.asks()
    assert shell.texts()[0] == f"fidelity: ok {HEAD} carried from {OLD[:7]} (cci #170001), patch-identical by git patch-id over its own commits."


PIPELINE_TEST_ONLY = {"seq": 170003, "lane": fidelity.LANE, "text": f"fidelity: ok {OLD} {TEST_ONLY_PHRASE}: carried from {MOVED[:7]} (cci #170000), patch-identical by git patch-id over its own commits."}
FIRST_TEST_ONLY = {"seq": 170000, "lane": fidelity.LANE, "text": f"fidelity: ok {MOVED} {TEST_ONLY_PHRASE}: the added commit fixes a test."}
DESK_TAP_ROW = {"seq": 170004, "lane": "fidelity-desk-4", "text": f"fidelity: ok {MOVED} tap row: it ends a moved share's watch, an on-purpose old-app fix."}


@pytest.mark.parametrize(
    ("grades", "corrected", "said"),
    [
        ([FIRST_TEST_ONLY, PIPELINE_TEST_ONLY], [], f"{TEST_ONLY_PHRASE}: "),
        ([FIRST_TEST_ONLY, PIPELINE_TEST_ONLY, DESK_TAP_ROW], [], ""),
        ([FIRST_TEST_ONLY, PIPELINE_TEST_ONLY], [170003], ""),
        ([FIRST_TEST_ONLY, PIPELINE_TEST_ONLY], [170000], ""),
        ([FIRST_TEST_ONLY, PIPELINE_TEST_ONLY, {**DESK_TAP_ROW, "text": f"fidelity: ok {MOVED} {TEST_ONLY_PHRASE}: every commit is a test."}], [170003], ""),
    ],
    ids=["own-phrase", "desk-tap-row", "root-corrected", "root-corrected-upchain", "root-over-desk"],
)
def test_a_carry_takes_its_class_from_a_root_correction_then_the_newest_desk_grade_then_its_own_prior_phrase(tmp_path, grades, corrected, said):
    directory = job(tmp_path, grades=grades, corrected=corrected)
    shell = Fake(tmp_path, patches=SAME)
    assert fidelity.main([str(directory)], shell) == 0
    assert shell.texts()[0] == f"fidelity: ok {HEAD} {said}carried from {OLD[:7]} (cci #170003), patch-identical by git patch-id over its own commits."


SETTLED_POINT = "an empty listing returns TxNotAvailable at plan.rs:108 before subscribing"
OPEN_POINT = "replica.go:452 rejects a stopped share below its stop"
PIPELINE_MISSING = {"seq": 170006, "lane": fidelity.LANE, "text": f"fidelity: missing {OLD} plan:811,rule/8330d10 {SETTLED_POINT}; {OPEN_POINT}"}


PIPELINE_CARRIED = {"seq": 170008, "lane": fidelity.LANE, "text": f"fidelity: ok {OLD} carried from {MOVED[:7]} (cci #170006), patch-identical by git patch-id over its own commits, settled by root #170007."}


@pytest.mark.parametrize(
    ("grades", "settled", "carried"),
    [
        ([PIPELINE_MISSING], {"170006": 170007}, 170006),
        ([PIPELINE_MISSING | {"text": PIPELINE_MISSING["text"].replace(OLD, MOVED)}, PIPELINE_CARRIED], {"170006": 170007}, 170008),
        ([PIPELINE_MISSING], {}, 0),
    ],
    ids=["root-settled", "settled-up-the-chain", "unsettled"],
)
def test_a_patch_identical_head_carries_a_missing_root_settled_as_ok_naming_the_decide(tmp_path, grades, settled, carried):
    """#45403: df84649 carried 9464404's grade, whose missing root #200618 settled; the carry names the decide even when the grade it carries is itself a carry."""
    directory = job(tmp_path, grades=grades, settled=settled)
    shell = Fake(tmp_path, [reply()], SAME)
    fidelity.main([str(directory)], shell)
    if not settled:
        assert len(shell.asks()) == 1
        return
    assert not shell.asks()
    assert shell.texts()[0] == f"fidelity: ok {HEAD} carried from {OLD[:7]} (cci #{carried}), patch-identical by git patch-id over its own commits, settled by root #170007."


ANOTHER_PATCH = {(f"{OWN}^", HEAD): "5f1e", (f"{OLD}~1", OLD): "77aa"}
REALIGNED = {OLD_OWN[:7]: "diff --git a/types.go b/types.go\n+\tapiBase     *url.URL\n", OWN[:7]: "diff --git a/types.go b/types.go\n+\tapiBase       *url.URL\n"}
WAITS = {OLD_OWN[:7]: "diff --git a/copyup.rs b/copyup.rs\n+free(pass);\n", OWN[:7]: "diff --git a/copyup.rs b/copyup.rs\n+activity.idle().await;\n+free(pass);\n"}


@pytest.mark.parametrize("shows", [REALIGNED, WAITS], ids=["conflict-realigns-a-field", "own-commit-adds-a-wait"])
def test_a_settled_missing_carries_only_while_range_diff_finds_no_own_commit_changed(tmp_path, shows):
    """#45403: root #198135 settled the Route::post budget at da869d8, and 9464404's dev conflict only re-aligned a types.go field, yet its whole patch-id differed and the pipeline raised the budget again (#200402). #45468: c791478 added a wait to copyup.rs after root #200210 settled #200009, so that head is read again."""
    directory = job(tmp_path, grades=[PIPELINE_MISSING], settled={"170006": 170007})
    shell = Fake(tmp_path, [reply()], ANOTHER_PATCH)
    shell.shows = shows
    fidelity.main([str(directory)], shell)
    if shows is WAITS:
        assert len(shell.asks()) == 1
        question = (directory / "question-1.md").read_text()
        assert f"An earlier head, {OLD}, was reviewed." in question and f"only the hunks the range-diff shows for its pair ({OWN[:7]})" in question
        assert f"- {SETTLED_POINT} (root #170007)" in question
        return
    assert not shell.asks()
    assert shell.texts()[0] == f"fidelity: ok {HEAD} carried from {OLD[:7]} (cci #170006), patch-identical by git range-diff over its own commits, settled by root #170007."
    assert result(directory) == {"verdict": "carried", "from": OLD, "posted": ["#150002", "#150003"]}


def test_a_restack_whose_commits_range_diff_equal_carries_a_settled_missing_beside_a_desk_ok(tmp_path):
    """n-enumeration #45411: root #202312 settled #202152's TeamHost cache, production-env memo and landed index rows at a8a4812, which fidelity-desk-11 graded ok; the restack da4c8f1 pairs every commit equal by range-diff while dev moved their context, so its whole patch-id differs."""
    desk = {"seq": 170005, "lane": "fidelity-desk-4", "text": f"fidelity: ok {OLD} new-tree fix, stays a tap row."}
    directory = job(tmp_path, grades=[desk, PIPELINE_MISSING], settled={"170006": 170007})
    shell = Fake(tmp_path, patches=ANOTHER_PATCH)
    shell.range_diff = f"1:  {OLD_OWN[:7]} = 1:  {OWN[:7]} resubscribe\n"
    fidelity.main([str(directory)], shell)
    assert not shell.asks()
    assert shell.texts()[0] == f"fidelity: ok {HEAD} carried from {OLD[:7]} (cci #170006), patch-identical by git range-diff over its own commits, settled by root #170007."


@pytest.mark.parametrize(
    ("missing", "posted"),
    [
        ([SETTLED_POINT, OPEN_POINT], f"fidelity: missing {HEAD} plan:811 {SETTLED_POINT}; {OPEN_POINT}"),
        ([SETTLED_POINT], f"fidelity: ok {HEAD} Resubscribes from the fetched position after a cut. Every missing point is one root settled at #170007."),
        ([f"  {SETTLED_POINT} ", "a new point"], f"fidelity: missing {HEAD} plan:811 a new point"),
    ],
    ids=["unsettled-point-kept", "all-settled", "settled-dropped"],
)
def test_a_new_heads_missing_point_that_repeats_a_settled_one_word_for_word_is_dropped(tmp_path, missing, posted):
    settled = {"170006": 170007} if missing != [SETTLED_POINT, OPEN_POINT] else {}
    directory = job(tmp_path, grades=[PIPELINE_MISSING | {"text": f"fidelity: missing {OLD} plan:811 {SETTLED_POINT}"}], settled=settled)
    shell = Fake(tmp_path, [reply(verdict="missing" if missing else "ok", missing=tuple({"key": "plan:811", "line": line} for line in missing))], {(f"{OWN}^", HEAD): "5f1e", (f"{OLD}~1", OLD): "77aa"})
    fidelity.main([str(directory)], shell)
    assert shell.texts()[0] == posted
    if settled:
        assert f"- {SETTLED_POINT} (root #170007)" in (directory / "question-1.md").read_text()


@pytest.mark.parametrize(
    ("grades", "patches"),
    [
        ([DESK_OK], {(f"{OWN}^", HEAD): "5f1e", (f"{OLD}~1", OLD): "77aa"}),
        ([DESK_OK, {"seq": 170005, "lane": "fidelity-desk-4", "text": f"fidelity: missing {OLD} rule/949e6be polls the signal"}], SAME),
        ([{"seq": 170005, "lane": "fidelity-desk-4", "text": f"fidelity: missing {OLD} rule/949e6be polls the signal"}], SAME),
    ],
    ids=["another-patch", "one-lane-missing", "only-missing"],
)
def test_a_head_that_matches_no_head_every_lane_graded_ok_is_read_by_codex(tmp_path, grades, patches):
    directory = job(tmp_path, grades=grades)
    shell = Fake(tmp_path, [reply()], patches)
    fidelity.main([str(directory)], shell)
    [ask] = shell.asks()
    assert ask[:6] == ["codex-ask", "-m", "sol", "--incident", "--schema", str(fidelity.SCHEMA)]
    assert shell.texts()[0].startswith(f"fidelity: ok {HEAD} Resubscribes")


def test_a_head_past_one_every_lane_graded_ok_is_graded_on_its_range_diff_delta_alone(tmp_path):
    """p8a-step7 #196587: #44103's later heads carried 17 files and 2,371 lines, and a whole-PR pass answered reader instead of grading."""
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()], patches={})
    shell.range_diff = f"1:  {OLD_OWN[:7]} ! 1:  {OWN[:7]} resubscribe\n"
    shell.shows = {OLD_OWN[:7]: "diff --git a/f.go b/f.go\n+old\n", OWN[:7]: "diff --git a/f.go b/f.go\n+new\n"}
    fidelity.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"An earlier head, {OLD}, was reviewed. `git range-diff {OLD_OWN}^..{OLD} {OWN}^..{HEAD}`" in question
    assert f"only the hunks the range-diff shows for its pair ({OWN[:7]})" in question
    assert shell.texts()[0].startswith(f"fidelity: ok {HEAD} Resubscribes")


def test_a_codex_ok_posts_the_grade_with_its_exempt_phrase_then_touches_and_old_app(tmp_path):
    directory = job(tmp_path, grades=[])
    shell = Fake(tmp_path, [reply(exempt="test-only", touches=("prompt/1026-105052",), old_app="changes")])
    fidelity.main([str(directory)], shell)
    assert shell.texts() == [
        f"fidelity: ok {HEAD} {TEST_ONLY_PHRASE}: Resubscribes from the fetched position after a cut.",
        "touches prompt/1026-105052",
        f"old-app: CHANGES {HEAD[:7]} baseline {BASELINE}; go/sand/next only",
    ]
    question = (directory / "question-1.md").read_text()
    assert str(fidelity.RUBRIC) in question and str(directory / "rows.md") in question and f"`git diff {OWN}^ {HEAD}`" in question
    assert "contract: builds the resubscribe" in question and "- go/sand/next/replica/follow.go: 41-43" in question
    assert "### Rulings\n- ports old-tree 304c1e7ea5e (#41725)" in question and "Body addendum\nThe caller trace" in question and "ci is green" not in question
    assert "package, /plans/durable." in question and "`subplans/`" in question
    assert f"checked {','.join(KEYS)} names poll|resubscribe" in (directory / "record.md").read_text()
    assert result(directory) == {"verdict": "ok", "posted": ["#150002", "#150003", "#150004"]}


def test_a_missing_verdict_posts_one_owner_grounded_line_naming_every_broken_row_so_one_resolve_cannot_skip_a_row(tmp_path):
    broken = ({"key": "rule/949e6be", "line": "follow.go:41 polls the signal, 'not polled'"}, {"key": "plan:212", "line": "plan names no retry counter"}, {"key": "plan:subplans/signal-service.md:45", "line": "signal.rs lives in sandsql-server, not its own server"})
    directory = job(tmp_path, grades=[], baseline="")
    shell = Fake(tmp_path, [reply("missing", broken)])
    fidelity.main([str(directory)], shell)
    missing, old_app = shell.texts()
    assert missing == f"fidelity: missing {HEAD} rule/949e6be,plan:212,plan:subplans/signal-service.md:45 follow.go:41 polls the signal, 'not polled'; plan names no retry counter; signal.rs lives in sandsql-server, not its own server"
    assert owner_grounded(missing) and old_app == f"old-app: unchanged {HEAD[:7]} baseline the old app as last released; go/sand/next only"


def test_a_head_codex_cannot_settle_goes_to_the_human_fidelity_desks(tmp_path):
    directory = job(tmp_path, grades=[], baseline="")
    shell = Fake(tmp_path, [reply("reader", reader="deletes the eviction path, a reserved row no answer authorizes", touches=("rule/949e6be",))])
    fidelity.main([str(directory)], shell)
    reader, touches, _ = shell.posted
    assert reader["--text"] == f"{fidelity.READER} {HEAD} deletes the eviction path, a reserved row no answer authorizes"
    assert reader["--to"] == "p8a-step9,desk-runner,board-refresher-2,fidelity-desk-4,fidelity-desk-5" and touches["--to"] == "p8a-step9,desk-runner,board-refresher-2"


@pytest.mark.parametrize(
    "bad",
    [
        reply(checked=KEYS[:1]),
        reply("missing", ({"key": "cci#172451", "line": "root said so"},)),
        reply("missing", ({"key": "rule/1234567", "line": "a row the rows file does not carry"},)),
        reply("missing"),
        reply("reader"),
        reply() | {"swept": "looks fine"},
    ],
    ids=["unchecked-row", "root-record-key", "row-outside-the-slice", "missing-without-entries", "reader-without-reason", "no-reasoning"],
)
def test_a_reply_that_breaks_the_grade_contract_is_asked_once_more_and_a_second_fails_the_job(tmp_path, bad):
    directory = job(tmp_path, grades=[])
    shell = Fake(tmp_path, [bad, reply()])
    fidelity.main([str(directory)], shell)
    assert len(shell.asks()) == 2 and "Your last reply gave" in (directory / "question-2.md").read_text()
    assert shell.texts()[0].startswith(f"fidelity: ok {HEAD}")
    failing = job(tmp_path / "again", grades=[])
    with pytest.raises(finder.JobFailed):
        fidelity.main([str(failing)], Fake(tmp_path, [bad, bad]))
    assert result(failing)["error"].startswith("JobFailed: two replies gave")


@pytest.mark.parametrize(("state", "heads"), [("CLOSED", (HEAD,)), ("OPEN", (MOVED,)), ("OPEN", (HEAD, MOVED))], ids=["closed", "moved", "moved-while-read"])
def test_a_closed_pr_or_a_head_that_moved_posts_nothing(tmp_path, state, heads):
    directory = job(tmp_path, grades=[])
    shell = Fake(tmp_path, [reply()], state=state, heads=heads)
    fidelity.main([str(directory)], shell)
    assert not shell.posted and "skipped" in result(directory)


def test_a_head_whose_pr_has_an_open_sweep_finding_grades_missing_naming_it_with_no_model_call(tmp_path, monkeypatch):
    """Owner 3:06 AM Oct 30 and root #205185: a sweep's finding on a PR was lost while the PR graded ok and landed."""
    row = {"seq": "205428", "sweep": "local-state-sweep-1", "owner": "naming-diet-2", "text": "dev host /_ssql route\nlives in a JSON file under ~/.iris", "pr": "43299", "state": "open"}
    monkeypatch.setattr(fidelity, "open_findings", lambda job: [row] if job["pr"] == "43299" else [])
    directory = job(tmp_path, grades=[])
    shell = Fake(tmp_path, [reply()])

    fidelity.main([str(directory)], shell)

    assert shell.texts() == [f"fidelity: missing {HEAD} finding/205428 open finding 205428 from local-state-sweep-1, owner naming-diet-2: dev host /_ssql route lives in a JSON file under ~/.iris"]
    assert not shell.asks() and result(directory)["verdict"] == "missing"
