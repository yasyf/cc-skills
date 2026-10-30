from milestones import compute

FIRST = {"name": "Cloud keeps working", "release": "R1", "live_match": r"\bapi\b"}
SECOND = {"name": "New Mac app", "release": "R2", "changes": [7, 8, 9], "testing_match": r"sand/next", "live_match": r"switched on"}


def release(seq, text, at="2026-10-29T20:00:00Z", lane="release-worker"):
    return {"seq": seq, "kind": "release", "lane": lane, "at": at, "text": text}


def blocker(seq, milestone, owner, next_step, text, status="open", at="2026-10-29T21:00:00Z"):
    return {"seq": seq, "kind": "blocker", "lane": "root", "at": at, "text": text, "to": [owner], "status": status, "fields": {"attrs": {"milestone": milestone, "owner": owner, "next": next_step}}}


def prs(**states):
    return [{"number": int(n[1:]), "state": s} for n, s in states.items()]


def test_an_open_blocker_record_makes_a_milestone_blocked_and_says_what_unblocks_it():
    rows = compute([FIRST], [], [blocker(5, "R1", "fixer", "The fixer ships the patch by 7 PM", "Runs fail on start")], [release(1, "10970 passed: api live")])
    assert rows[0]["state"] == "broken"
    assert rows[0]["waits"] == "Runs fail on start"
    assert rows[0]["unblocking"] == "The fixer ships the patch by 7 PM"
    assert rows[0]["owners"] == ["fixer"]


def test_a_cleared_blocker_no_longer_blocks():
    cleared = blocker(6, "R1", "fixer", "", "Runs work again")
    cleared["fields"]["attrs"]["cleared"] = "yes"
    rows = compute([FIRST], [], [blocker(5, "R1", "fixer", "x", "y"), cleared], [release(1, "10970 passed: api live")])
    assert rows[0]["state"] == "live"


def test_a_blocker_answered_by_a_worker_still_blocks():
    rows = compute([FIRST], [], [blocker(5, "R1", "fixer", "x", "Runs fail on start", status="closed")], [release(1, "10970 passed: api live")])
    assert rows[0]["state"] == "broken"


def test_the_newest_blocker_on_a_topic_replaces_the_older_one():
    rows = compute([FIRST], [], [blocker(5, "R1", "fixer", "x", "Old wording"), blocker(6, "R1", "fixer", "x", "New wording")], [release(1, "10970 passed: api live")])
    assert rows[0]["waits"] == "New wording"


def test_a_milestone_stays_live_while_a_newer_release_runs():
    rows = compute([FIRST], [], [], [release(1, "10970 passed: api live"), release(2, "RELEASE 10980 started: api", at="2026-10-29T22:00:00Z")])
    assert rows[0]["state"] == "live"


def test_a_release_that_started_and_never_finished_reads_releasing():
    rows = compute([FIRST], [], [], [release(2, "RELEASE 10980 started: api", at="2026-10-29T22:00:00Z")])
    assert (rows[0]["state"], rows[0]["label"]) == ("progress", "Releasing")


def test_open_changes_are_counted_by_their_merge_state():
    rows = compute([FIRST, SECOND], prs(n7="landed", n8="queued", n9="failing"), [], [release(1, "api passed")])
    assert rows[1]["label"] == "Being built"
    assert rows[1]["waits"] == "2 of 3 changes still open: 1 in the merge queue, 1 with a failing build"


def test_a_published_test_build_reads_testing_until_the_switch_turns_on():
    landed = prs(n7="landed", n8="landed", n9="landed")
    rows = compute([FIRST, SECOND], landed, [], [release(1, "api passed"), release(3, "LIVE: new build on sand/next.json")])
    assert rows[1]["label"] == "Testing"
    rows = compute([FIRST, SECOND], landed, [], [release(1, "api passed"), release(3, "LIVE: new build on sand/next.json"), release(4, "LIVE: release-channel switched on for Platform")])
    assert rows[1]["state"] == "live"


def test_a_milestone_waits_on_the_one_before_it():
    rows = compute([FIRST, {"name": "Later", "release": "R3"}], [], [], [])
    assert rows[1]["state"] == "not"
    assert rows[1]["waits"] == "Cloud keeps working first"


def test_a_change_waiting_on_a_tap_reads_in_plain_words():
    rows = compute([FIRST, SECOND], prs(n7="landed", n8="you", n9="landed"), [], [release(1, "api passed")])
    assert rows[1]["waits"] == "1 of 3 changes still open: 1 waiting on your tap"
    assert rows[1]["unblocking"] == "You tap the ones on your board"


def test_a_reposted_blocker_keeps_the_time_the_problem_started():
    record = blocker(5, "R1", "fixer", "x", "Runs fail on start", at="2026-10-30T03:00:00Z")
    record["fields"]["attrs"]["since"] = "2026-10-30T01:58:00Z"
    rows = compute([FIRST], [], [record], [release(1, "10970 passed: api live")])
    assert rows[0]["since"] == "2026-10-30T01:58:00Z"


def test_a_live_milestone_is_live_since_its_first_release():
    rows = compute([FIRST], [], [], [release(1, "10970 passed: api live", at="2026-10-28T12:00:00Z"), release(2, "10980 passed: api live", at="2026-10-29T22:00:00Z")])
    assert rows[0]["since"] == "2026-10-28T12:00:00Z"


def test_a_blocker_without_a_promised_time_says_so_and_since_when():
    rows = compute([FIRST], [], [blocker(5, "R1", "fixer", "The fixer finds the cause", "Runs fail on start")], [release(1, "10970 passed: api live")])
    assert rows[0]["unblocking"] == "The fixer finds the cause, no time given since 2:00 PM Oct 29"


def test_a_blocker_with_a_promised_time_shows_it():
    record = blocker(5, "R1", "fixer", "The fixer ships the patch", "Runs fail on start")
    record["fields"]["attrs"]["by"] = "2026-10-30T04:30:00Z"
    rows = compute([FIRST], [], [record], [release(1, "10970 passed: api live")])
    assert rows[0]["unblocking"] == "The fixer ships the patch, by 9:30 PM Oct 29"


def test_a_promised_time_written_in_words_is_shown_as_written():
    record = blocker(5, "R1", "fixer", "The fixer ships the patch", "Runs fail on start")
    record["fields"]["attrs"]["by"] = "within 15 minutes of your word"
    rows = compute([FIRST], [], [record], [release(1, "10970 passed: api live")])
    assert rows[0]["unblocking"] == "The fixer ships the patch, within 15 minutes of your word"
