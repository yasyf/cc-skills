import pytest
from brief import Curator, slot_of
from plainwords import violations

LANES = ("p8f-replica-4", "landing-desk-3")


@pytest.mark.parametrize(
    "text",
    [
        "WAITS ON RED R1",
        "p8f-replica-4 HEAD 38f7ec6",
        "hold records #134860-#134881",
        "Tonight stops at R3",
        "#42723 sand: approved, waiting to land",
        "Fix in go/sand/internal/vm/run.go is pushed",
        "Last passed at 2026-10-27T08:01Z",
        "The landing desk queued it",
        "Restacked three changes onto the new base",
        "The new tree builds again",
        "on it for 45 h 0 min",
    ],
)
def test_walk_strings_fail(text):
    assert violations(text, "headline", LANES)


@pytest.mark.parametrize(
    "text",
    [
        "Two milestones are live. The new Iris Mac app is stuck on two problems.",
        "Faster run lookups, the first small cloud update, is still in review.",
        "Coordinator told at 12:14 PM; nobody assigned yet, no time given.",
        "SandSQL server change must merge and ship in the next release",
        "Waits on a reviewer for 2 h 5 min",
    ],
)
def test_rewrites_pass(text):
    assert violations(text, "headline", LANES) == []


def test_word_limits_follow_the_slot():
    text = "one two three four five six seven eight nine"
    assert violations(text, "title") == ["9 words where the limit is 8"]
    assert violations(text, "latest") == []


def test_a_failing_string_keeps_the_last_passing_one(tmp_path):
    curator = Curator({"brief_dir": str(tmp_path)}, None, None)
    curator.brief = {"headline": "Two milestones are live.", "work": [{"title": "Faster run lookups", "latest": "In its last review."}]}
    fresh = {"headline": "R3 is held", "work": [{"title": "Faster run lookups", "latest": "landing-desk-3 queued it"}]}
    problems = ["headline: 'R3 is held' has a release or plan code", "work.0.latest: 'landing-desk-3 queued it' has the coined word 'desk'"]
    kept = curator.keep_passing(fresh, problems, LANES)
    assert kept == {"headline": "Two milestones are live.", "work": [{"title": "Faster run lookups", "latest": "In its last review."}]}


def test_slots_cover_every_rendered_field():
    assert slot_of(("paths", 0, "nodes", 1, "how")) == "how"
    assert slot_of(("paths", 0, "nodes", 1, "item")) is None
    assert slot_of(("work", 2, "workers", 0)) is None
    assert slot_of(("progress", "stops", 3, "label")) == "line"


def test_an_unnamed_part_fails():
    assert violations("The first milestone's second part waits on two changes", "headline") == ["an unnamed part"]


@pytest.mark.parametrize(
    "text",
    [
        "The merge process names the finding; the agent machines worker answers it",
        "The agent machines worker answers the review",
        "The cloud runs worker fixes its failing tests",
        "The agent machines worker settles the review hold; merge process lands it",
    ],
)
def test_a_fix_that_does_not_say_what_it_is_fails(text):
    assert "a fix that does not say what it fixes" in violations(text, "how")


@pytest.mark.parametrize(
    "text",
    [
        "The agent machines worker makes a failed restore retry",
        "The cloud runs worker moves 22 files off the deleted session store",
        "The worker answers the review's finding that restores can fail",
    ],
)
def test_a_fix_that_says_what_it_is_passes(text):
    assert violations(text, "how") == []
