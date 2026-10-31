from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import merge_queue


class Shell:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.slept = []

    def run(self, command):
        self.calls.append(command)
        return next(self.responses)

    def sleep(self, seconds):
        self.slept.append(seconds)


def done(out="", code=0, err=""):
    return SimpleNamespace(out=out, code=code, err=err)


def adapter(shell):
    return merge_queue.TrunkStackEnqueue(shell, Path("/repo"), {"script": "stack-enqueue"}, None, "Forge-AI/monorepo")


def verdict(detail, state="BLOCKED"):
    return {"pr": "20", "sha": "abcdef1234", "detail": detail, "verdict": state}


def test_native_prefix_is_read_without_the_legacy_partial_flag():
    shell = Shell(done("#10 GREEN aaaa111111 Trunk notSubmitted\n#20 BLOCKED bbbb222222 ai-review in progress\nwould enqueue native stack through #10: #10\n"))
    gate = adapter(shell).check(["20"], False, {}, True)
    assert gate.would == ["10"]
    assert gate.verdicts["20"]["verdict"] == "BLOCKED"
    assert shell.calls == [["/repo/stack-enqueue", "20", "--check"]]


def test_native_enqueue_distinguishes_an_admission_from_already_queued():
    shell = Shell(done("#20 QUEUED abcdef1234 native stack covers #10 #20\n"), done("#20 QUEUED abcdef1234 Trunk QUEUED\nnothing to enqueue: Trunk already holds the green bottom prefix\n"))
    queue = adapter(shell)
    assert queue.enqueue(["20"], False, {})[0] == "enqueued"
    assert queue.enqueue(["20"], False, {})[0] == "noop"
    assert all("--partial" not in command for command in shell.calls)


def test_queue_membership_uses_authoritative_active_flag_instead_of_state_names():
    shell = Shell(done(json.dumps({"10": {"isCurrentlySubmittedToQueue": True, "queueEntry": {"state": "waitingForUnknownFutureStage"}}, "20": {"isCurrentlySubmittedToQueue": False, "queueEntry": {"state": "merged"}}})))
    assert adapter(shell).queued(["10", "20"]) == {"10": True, "20": False}
    assert shell.calls == [["/repo/tools/ci", "trunk", "status", "10", "20"]]


def test_failed_queue_read_stays_unknown_and_never_retries_a_mutation():
    shell = Shell(done(code=5, err="HTTP 503"), done(code=5, err="HTTP 503"))
    queue = adapter(shell)
    assert queue.queued(["20"]) is None
    assert queue.enqueue(["20"], False, {})[0] == "failed"
    assert len(shell.calls) == 2


def test_missing_queue_record_stays_unknown():
    assert adapter(Shell(done("{}"))).queued(["20"]) is None


def test_required_check_failure_and_pending_remain_distinct():
    queue = adapter(Shell())
    assert queue.classify(verdict("required check test is FAILURE"))["required-ci"].state == merge_queue.FAIL
    assert queue.classify(verdict("required check test is IN_PROGRESS"))["required-ci"].state == merge_queue.PENDING
    assert queue.classify(verdict("GitHub returned no required checks"))["required-ci"].state == merge_queue.PENDING


def test_failed_verification_is_not_guessed_to_be_a_flake_or_an_own_red():
    queue = adapter(Shell())
    failed = verdict("Trunk's last attempt failed; inspect its verification run before an explicit --flake retry")
    assert queue.ejection(failed, {"20"}) is None
    assert queue.blocker(failed, {"20"}, False) is not None
    assert queue.release("20", "abcdef1234")[0] is False
    assert queue.recover(["20"])[0] != 0


def test_a_failed_trunk_attempt_is_a_blocker_but_never_a_bare_eviction_a_down_queue_lands_directly():
    """Root #228786: stack-enqueue prints `Trunk attempt <time> failed: <reason>` for a failed head, and the adapter routed none of it."""
    queue = adapter(Shell())
    failed = verdict("Trunk attempt 2026-10-30T23:10:00Z failed: TEST_RUN_REQUIRED_STATUS_FAILED (verification v1); inspect its verification and push a fix or explicitly name this PR with --flake")
    assert queue.blocker(failed, {"20"}, False) == merge_queue.Blocker(failed["detail"], False)
    assert not queue.evicted_only(failed)
    assert queue.ejection(failed, {"20"}) is None


def test_an_enqueue_trunk_answers_http_429_ends_rate_limited_and_is_never_retried_in_the_pass():
    shell = Shell(done("submission not confirmed: ci trunk: Trunk GET /v2/pull-requests?limit=100&number=20 answered HTTP 429 (): ; request; inspect Trunk before retrying\n", code=5))
    assert adapter(shell).enqueue(["20"], False, {})[0] == merge_queue.RATE_LIMITED
    assert shell.calls == [["/repo/stack-enqueue", "20"]] and shell.slept == []


def test_known_native_conflict_routes_as_conflict():
    queue = adapter(Shell())
    failed = verdict("GitHub mergeability CONFLICTING; run ccx vcs stack submit for conflicts")
    assert queue.blocker(failed, {"20"}, False).conflict
    assert queue.ejection(failed, {"20"}).kind == merge_queue.CONFLICT


def test_registry_preserves_legacy_adapter_and_selects_native_explicitly():
    shell = Shell()
    native = merge_queue.adapter(shell, Path("/repo"), {"kind": "trunk-stack-enqueue", "script": "stack-enqueue"}, None, "o/r", "ledger")
    legacy = merge_queue.adapter(shell, Path("/repo"), {"kind": "graphite-stack-enqueue", "script": "stack-enqueue"}, None, "o/r", "ledger")
    assert isinstance(native, merge_queue.TrunkStackEnqueue)
    assert type(legacy) is merge_queue.GraphiteStackEnqueue


def test_suggested_queue_kind_follows_explicit_repo_queue_selection(tmp_path, monkeypatch):
    script = tmp_path / merge_queue.STACK_ENQUEUE
    script.parent.mkdir(parents=True)
    script.write_text("")
    monkeypatch.setattr(merge_queue.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="trunk\n", stderr=""))
    assert merge_queue.suggested_adapter(tmp_path)["kind"] == "trunk-stack-enqueue"
    monkeypatch.setattr(merge_queue.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert merge_queue.suggested_adapter(tmp_path)["kind"] == "graphite-stack-enqueue"
    (tmp_path / merge_queue.CCX_CONFIG).parent.mkdir()
    (tmp_path / merge_queue.CCX_CONFIG).write_text('[vcs]\nqueue = "trunk"\n')
    assert merge_queue.suggested_adapter(tmp_path)["kind"] == "trunk-stack-enqueue"
    monkeypatch.setattr(merge_queue.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="graphite\n", stderr=""))
    assert merge_queue.suggested_adapter(tmp_path)["kind"] == "graphite-stack-enqueue"


def test_native_dequeue_refuses_stale_head_instead_of_accepting_legacy_exit_one():
    shell = Shell(done("#20 BLOCKED Trunk indexed aaaa111111 instead of GitHub head bbbb222222\n", code=1))
    released, reason = adapter(shell).dequeue("20")
    assert released is False
    assert "BLOCKED" in reason
    assert shell.calls == [["/repo/stack-enqueue", "20", "--dequeue"]]


def test_native_dequeue_accepts_only_its_exact_already_absent_receipt_on_exit_one():
    shell = Shell(done("#20 NOT_QUEUED\n", code=1), done("#21 NOT_QUEUED\n", code=1), done("#20 DEQUEUED abcdef1234\n", code=0))
    queue = adapter(shell)
    assert queue.dequeue("20")[0] is True
    assert queue.dequeue("20")[0] is False
    assert queue.dequeue("20")[0] is True


def test_legacy_dequeue_retains_its_existing_exit_one_contract():
    shell = Shell(done("Graphite released its queue entry\n", code=1))
    queue = merge_queue.GraphiteStackEnqueue(shell, Path("/repo"), {"script": "stack-enqueue"}, None, "o/r")
    assert queue.dequeue("20")[0] is True


def test_a_tls_handshake_timeout_reaching_trunk_is_retried_instead_of_failing_the_check():
    """Root #222205: ci trunk TLS handshake timeouts under load read as GATE-FAILED with no retry."""
    timeout = done(err='stack-enqueue failed: ci trunk: Get "https://api.trunk.io/v2/merge-queues?limit=100": net/http: TLS handshake timeout\n', code=5)
    shell = Shell(timeout, done("#10 GREEN aaaa111111 Trunk notSubmitted\nwould enqueue native stack through #10: #10\n"))
    gate = adapter(shell).check(["10"], False, {}, False)
    assert gate.would == ["10"] and len(shell.calls) == 2 and shell.slept == [merge_queue.TRANSIENT_PAUSE_SECONDS]
