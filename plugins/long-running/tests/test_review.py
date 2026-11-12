import queue
import subprocess
from pathlib import Path

import pytest
from drive import review

KEY = "yasyf/cc-skills#41"
STARTED = "http://127.0.0.1:5/s/r41\ntailnet: https://box.tail1.ts.net:5/s/r41\n"


@pytest.fixture
def assistant(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> review.Assistant:
    monkeypatch.setattr(review.Assistant, "warm_loop", lambda self: None)
    monkeypatch.setattr(review, "cc_review", lambda: "cc-review")
    return review.Assistant("demo")


def test_a_review_starts_again_only_for_a_new_head_or_when_its_link_stops_answering(assistant: review.Assistant, monkeypatch: pytest.MonkeyPatch) -> None:
    started, answering = [], {"now": True}
    monkeypatch.setattr(review.subprocess, "run", lambda argv, **kwargs: started.append(argv) or subprocess.CompletedProcess(argv, 0, STARTED, ""))
    monkeypatch.setattr(review, "live", lambda url: answering["now"])
    links = [assistant.url(KEY, "h1", viewing=False), assistant.url(KEY, "h1", viewing=False)]
    answering["now"] = False
    links.append(assistant.url(KEY, "h1", viewing=False))
    links.append(assistant.url(KEY, "h2", viewing=False))
    scope = assistant.scope(KEY)
    assert links == ["https://box.tail1.ts.net:5/s/r41"] * 4
    assert started == [["cc-review", "start", "--pr", KEY, "--session", assistant.session(KEY), "--cwd", str(scope)]] * 3
    assert scope.is_dir() and scope.parts[-3:] == ("yasyf", "cc-skills", "41")


def test_warming_queues_each_pr_once_per_head(assistant: review.Assistant) -> None:
    assistant.warm([(KEY, "h1"), ("yasyf/cc-skills#42", "h9")])
    assistant.warm([(KEY, "h1"), (KEY, "h2")])
    assert [assistant.warming.get_nowait() for _ in range(assistant.warming.qsize())] == [(KEY, "h1"), ("yasyf/cc-skills#42", "h9"), (KEY, "h2")]


def test_a_restarted_dashboard_warms_only_reviews_not_started_at_their_head(assistant: review.Assistant, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, STARTED, ""))
    assistant.url(KEY, "h1", viewing=False)
    restarted = review.Assistant("demo")
    restarted.warm([(KEY, "h1"), (KEY, "h2")])
    assert [restarted.warming.get_nowait() for _ in range(restarted.warming.qsize())] == [(KEY, "h2")]


def test_his_requests_are_served_once_per_attempt_and_only_his_own_comments(assistant: review.Assistant) -> None:
    assistant.queues[KEY] = queue.Queue()
    asked = {"type": "ai.request.created", "request": {"id": "q1", "attempt": 1, "text": "why a new process?"}}
    answered = {"type": "ai.request.created", "request": {"id": "q1", "attempt": 2, "status": "answered"}}
    his = {"type": "comment.created", "id": "c1", "comment": {"author": "user", "body": "is this flat per machine?"}}
    coworker = {"type": "comment.created", "id": "c2", "comment": {"author": "remote", "body": "nit"}}
    for event in (asked, asked, answered, his, his, coworker, {"type": "status.changed", "status": "closed"}):
        assistant.offer(KEY, event)
    served = assistant.queues[KEY]
    assert [served.get_nowait() for _ in range(served.qsize())] == [asked, answered, his]


@pytest.mark.parametrize(
    ("said", "shown"),
    [
        ("gh: API rate limit exceeded for user", "GitHub's API rate limit for your account is used up, so cc-review could not read the PR until it resets within the hour (cc-review said: gh: API rate limit exceeded for user)"),
        ("Get https://api.github.com: net/http: TLS handshake timeout", "GitHub did not answer cc-review in time (cc-review said: Get https://api.github.com: net/http: TLS handshake timeout)"),
        ("fetch pr: context deadline exceeded", "GitHub did not answer cc-review in time (cc-review said: fetch pr: context deadline exceeded)"),
        ("no such PR", "cc-review said: no such PR"),
    ],
)
def test_a_review_that_cannot_start_says_why_in_plain_words(assistant: review.Assistant, monkeypatch: pytest.MonkeyPatch, said: str, shown: str) -> None:
    monkeypatch.setattr(review.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", f"{said}\n"))
    with pytest.raises(review.DriveError) as refused:
        assistant.url(KEY, "h1", viewing=False)
    assert str(refused.value) == shown


def test_a_review_that_takes_too_long_to_start_says_the_dashboard_stopped_waiting(assistant: review.Assistant, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(argv, review.START_SECONDS)

    monkeypatch.setattr(review.subprocess, "run", slow)
    with pytest.raises(review.DriveError, match="^cc-review took more than 2 minutes to open the review, so the dashboard stopped waiting$"):
        assistant.url(KEY, "h1", viewing=False)
