import json

import pytest
from conftest import Jev

from drive import settled

RULE = "A merge lands only on his tap."
CALL = {"tool": "Bash", "input": {"command": "gh pr merge 42 --admin", "description": "Merge it"}}
CLAIM = {"claim": "The cloud's inbox reader queries Postgres straight.", "read": "read:1"}
CALL_READ = 'ccx_code_read {"path": "go/sand/next/internal/cloudrunner/inbox.go"}'
RESULT = "57  rows, err := r.postgres.Query(ctx, selectInbox, agentID)"
READ = {"id": "read:1", "read": "cloudrunner/inbox.go", "source": "a file", "quote": f"{CALL_READ}\n{RESULT}"}
FINDING = {"rule": "A line names each thing he could open with its link.", "found": '"the first PR" names something he could open with no link there'}


def handled(text: str, reads: list[dict] | None = None) -> dict:
    return {"post": {"text": text, "claims": [CLAIM]}, "reads": [READ] if reads is None else reads, "his_words": []}


def test_weighed_gives_jev_at_most_the_seconds_the_hook_has_left_and_a_refusal_the_refusal_seconds_at_most(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], float]] = []

    def heard(told: dict, questions: dict[str, settled.Noul], seconds: float = settled.DECIDE_SECONDS) -> dict[str, float]:
        seen.append((list(questions), seconds))
        return {key: 0.05 for key in questions}

    monkeypatch.setattr(settled, "jev", heard)
    refusal = {"refusal": {"rule": RULE, "call": CALL}}
    post = {"post": {"post": {"text": "Merged."}, "findings": [FINDING]}}
    verdicts = [settled.weighed(refusal | {"seconds": 1.5}), settled.weighed(refusal), settled.weighed(post | {"seconds": 7.0}), settled.weighed(post)]
    assert seen == [(["protects"], 1.5), (["protects"], settled.REFUSAL_SECONDS), (["f1.holds"], 7.0), (["f1.holds"], settled.DECIDE_SECONDS)]
    assert verdicts == [{"verdict": "pass", "note": ""}] * 4


@pytest.mark.parametrize(
    ("sure", "verdict"),
    [
        (0.9, {"verdict": "hold", "note": ""}),
        (0.5, {"verdict": "hold", "note": "Jev is unsure whether this call is what its rule protects against, so the refusal stands."}),
        (0.1, {"verdict": "pass", "note": ""}),
    ],
)
def test_a_refusal_stands_at_yes_and_in_the_band_and_lets_the_call_through_under_no_by_one_noul_over_its_rule_and_whole_call(jev: Jev, sure: float, verdict: dict) -> None:
    jev.breaks("protects", "", sure)
    assert settled.refusal_verdict(RULE, CALL) == verdict
    [request] = jev.asked
    assert request == {"state": {"rule": RULE, "call": CALL}, "questions": {"protects": settled.PROTECTS}}


def test_a_refusal_jev_gives_no_verdict_on_only_warns_so_the_call_goes_ahead(jev: Jev) -> None:
    jev.answer = None
    assert settled.refusal_verdict(RULE, CALL) == {
        "verdict": "warn",
        "note": "Jev gave no verdict (no jev decision within 20s) on whether this call is what the refusal's rule protects against, so the call goes ahead and the refusal only warns.",
    }


def test_a_handlers_claim_goes_to_jev_beside_its_reads_whole_call_and_holds_under_backs_and_stands_at_it_never_only_warning(jev: Jev) -> None:
    output = handled(CLAIM["claim"])
    held = settled.post_verdict(output)
    assert held == {
        "verdict": "hold",
        "note": f'Jev held back this post: claim "{CLAIM["claim"]}" does not stand on its read: make the read that shows it and name it in claims, or cut the line to what your reads show. So fix each, then call say again.',
    }
    assert jev.asked[-1] == {
        "state": {
            "post": {"text": CLAIM["claim"], "sentences": [CLAIM["claim"]]},
            "claims": {"c1": {"claim": CLAIM["claim"], "read": "read:1"}},
            "reads": {"read:1": {"call": CALL_READ, "result": RESULT}},
        },
        "questions": {"c1.backed": settled.asking(settled.BACKED, read="read:1", claim="c1")},
    }
    jev.breaks("c1", "backed", settled.BACKS - 0.01)
    assert settled.post_verdict(output)["verdict"] == "hold"
    jev.breaks("c1", "backed", settled.BACKS)
    assert settled.post_verdict(output) == {"verdict": "pass", "note": ""}


def test_a_claim_whose_read_matched_none_of_the_handlers_calls_holds_unasked_and_a_sentence_no_claim_covers_is_asked_as_a_fact(jev: Jev) -> None:
    unread = settled.post_verdict(handled(CLAIM["claim"], [READ | {"quote": 'none: no call of the handler\'s own holds "cloudrunner/inbox.go"'}]))
    assert unread["note"].startswith('Jev held back this post: claim "The cloud\'s inbox reader queries Postgres straight." names the read "read:1", which matches none of your calls')
    assert jev.asked == []
    text = f"{CLAIM['claim']} The fix ships tonight."
    jev.picks("backed", 0.9)
    assert settled.post_verdict(handled(text)) == {"verdict": "pass", "note": ""}
    assert list(jev.asked[-1]["questions"]) == ["c1.backed", "s1.fact"]
    jev.picks("fact", 0.9)
    jev.breaks("c1", "backed", 0.9)
    assert '"The fix ships tonight." states a fact no claim names: name the read behind it in claims' in settled.post_verdict(handled(text))["note"]


def test_a_finding_holds_at_yes_unless_his_words_lift_it_and_jev_reads_his_words_beside_it(jev: Jev) -> None:
    reason = f"refused: {FINDING['found']}"
    output = {"post": {"text": "Waits on your Approve: the first PR."}, "findings": [FINDING], "his_words": [{"id": "w1", "quote": "land the first PR"}], "hookSpecificOutput": {"permissionDecisionReason": reason}}
    jev.breaks("f1", "holds", 0.9)
    assert settled.post_verdict(output)["note"].startswith(f"Jev held back this post: {FINDING['found']}, by its rule: {FINDING['rule']}. So fix each, then call say again.")
    assert (jev.asked[-1]["state"]["findings"], jev.asked[-1]["state"]["his_words"], list(jev.asked[-1]["questions"])) == ({"f1": FINDING}, {"w1": "land the first PR"}, ["f1.holds", "f1.lifted"])
    jev.picks("f1", 0.9)
    jev.ruled = {}
    stands = {"verdict": "warn", "note": "message_limits: Jev read no finding's rule as holding against this post, so it goes out as written."}
    assert settled.post_verdict(output) == stands
    jev.picks("lifted", 0.5)
    jev.breaks("f1", "holds", 0.1)
    assert settled.post_verdict(output) == stands


def test_a_post_jev_gives_no_verdict_on_holds_only_for_a_finding_a_count_decides(jev: Jev) -> None:
    jev.answer = None
    counted = "refused: this post is 69 words, and a line in his thread is at most 60 words"
    reason = f"refused: {FINDING['found']}"
    finding = {"post": {"text": "Merged."}, "findings": [FINDING], "his_words": [], "hookSpecificOutput": {"permissionDecisionReason": reason}}
    assert settled.post_verdict(finding | {"counted": counted})["verdict"] == "hold"
    assert settled.post_verdict(finding) == {
        "verdict": "warn",
        "note": f"message_limits: Jev gave no verdict (no jev decision within 20s), so each finding below only warns and the post goes out as written: if one holds, reword the post in place.\n```\n{reason}\n```",
    }
    assert settled.post_verdict(handled(CLAIM["claim"]))["note"].startswith("message_limits: Jev gave no verdict (no jev decision within 20s) on whether each claim of this post stands")


def test_each_read_with_no_room_is_named_by_the_read_whose_call_it_is_beside_that_calls_own_size_and_never_a_second_read_of_the_same_call() -> None:
    whole = "\n".join(f"{line}  rows, err := r.postgres.Query(ctx, selectInbox, agentID)" for line in range(1, 200))
    reads = [
        READ | {"quote": f"{CALL_READ}\n{whole}"},
        {"id": "read:2", "read": "inbox.go:57", "source": "a file", "quote": "the same call as read:1", "given": "read:1"},
        {"id": "read:3", "read": "outbox.go", "source": "a file", "quote": 'ccx_code_read {"path": "outbox.go"}\n12  ok'},
    ]
    claims = [{"claim": "The reader queries Postgres.", "read": "read:2"}, {"claim": "The outbox is fine.", "read": "read:3"}, {"claim": "It reads one row.", "read": "read:1"}]
    output = {"post": {"text": "The reader queries Postgres. The outbox is fine. It reads one row.", "claims": claims}, "reads": reads, "his_words": []}
    own = len(json.dumps({"state": {"reads": {"read:1": {"call": CALL_READ, "result": whole}}}, "questions": {}}, ensure_ascii=False))
    assert own > settled.REQUEST_CHARS
    assert settled.unfit_reads(output) == [('read:1 "cloudrunner/inbox.go"', own)]
