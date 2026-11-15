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


PASSES = {"verdict": "pass", "note": ""}


def handled(text: str, reads: list[dict] | None = None, claims: list[dict] | None = None) -> dict:
    return {"post": {"text": text, "claims": [CLAIM] if claims is None else claims}, "reads": [READ] if reads is None else reads, "his_words": []}


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


def test_a_handlers_claim_goes_to_jev_beside_its_reads_whole_output_and_holds_under_backs_and_stands_at_it_never_only_warning(jev: Jev) -> None:
    output = handled(CLAIM["claim"])
    held = settled.post_verdict(output)
    assert held == {
        "verdict": "hold",
        "note": f'Jev held back this post: claim "{CLAIM["claim"]}" does not stand on its read: make the read that shows it and name it in claims, or cut the line to what your reads show. So fix each, then call say again.',
    }
    assert jev.asked[-1] == {
        "state": {
            "post": {"text": CLAIM["claim"], "written": CLAIM["claim"]},
            "claims": {"c1": {"claim": CLAIM["claim"], "read": "read:1"}},
            "reads": {"read:1": {"call": CALL_READ, "result": RESULT}},
        },
        "questions": {"c1.backed": settled.asking(settled.BACKED, read="read:1", claim="c1")},
    }
    jev.breaks("c1", "backed", settled.BACKS - 0.01)
    assert settled.post_verdict(output)["verdict"] == "hold"
    jev.breaks("c1", "backed", settled.BACKS)
    assert settled.post_verdict(output) == PASSES


def test_a_claim_whose_read_matched_none_of_the_handlers_calls_holds_unasked_and_a_sentence_no_claim_holds_whole_is_asked_as_a_fact(jev: Jev) -> None:
    unread = settled.post_verdict(handled(CLAIM["claim"], [READ | {"quote": 'none: no call of the handler\'s own holds "cloudrunner/inbox.go"'}]))
    assert unread["note"].startswith('Jev held back this post: claim "The cloud\'s inbox reader queries Postgres straight." names the read "read:1", which matches none of your calls')
    assert jev.asked == []
    text = f"{CLAIM['claim']} The fix ships tonight."
    jev.picks("backed", 0.9)
    assert settled.post_verdict(handled(text)) == PASSES
    assert list(jev.asked[-1]["questions"]) == ["c1.backed", "s1.fact"]
    assert jev.asked[-1]["state"]["post"]["sentences"] == {"1": "The fix ships tonight."}
    jev.picks("fact", 0.9)
    jev.breaks("c1", "backed", 0.9)
    assert '"The fix ships tonight." states a fact no claim holds whole: pass that sentence, or each part of it, as claims beside the read behind each' in settled.post_verdict(handled(text))["note"]


def test_a_sentence_one_claim_holds_in_part_is_asked_as_a_fact_until_the_claims_of_its_parts_hold_it_whole_links_and_drive_references_aside(jev: Jev) -> None:
    sentence = "The cloud's inbox reader queries Postgres straight, and the pod reads the bucket {pr:7}."
    jev.picks("backed", 0.9)
    settled.post_verdict(handled(sentence))
    assert list(jev.asked[-1]["questions"]) == ["c1.backed", "s0.fact"]
    parts = [CLAIM, {"claim": "and the pod reads the bucket", "read": "read:1"}]
    settled.post_verdict(handled(sentence, claims=parts))
    assert list(jev.asked[-1]["questions"]) == ["c1.backed", "c2.backed"]


def test_a_claim_saying_only_every_no_or_never_of_a_set_is_asked_whether_its_read_reads_one_member_and_holds_at_yes(jev: Jev) -> None:
    only = {"claim": "Only the cloud's inbox reader queries Postgres straight.", "read": "read:1"}
    output = handled(only["claim"], claims=[only])
    jev.picks("backed", 0.9)
    assert settled.post_verdict(output) == PASSES
    assert jev.asked[-1]["questions"] == {
        "c1.backed": settled.asking(settled.BACKED, read="read:1", claim="c1"),
        "c1.spans": settled.asking(settled.SPANS, read="read:1", claim="c1"),
    }
    jev.breaks("c1", "spans", settled.YES)
    assert settled.post_verdict(output) == {
        "verdict": "hold",
        "note": f'Jev held back this post: claim "{only["claim"]}" speaks for a whole set while its read reads one member of it: name a read that finds every member, such as a grep across the code, or cut the claim to the member it reads. So fix each, then call say again.',
    }
    jev.breaks("c1", "spans", 0.5)
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f'message_limits: Jev is unsure of claim "{only["claim"]}" speaking for more of a set than its read reads, so the post goes out as written: if one is wrong, reword the post in place.',
    }
    settled.post_verdict(handled(CLAIM["claim"]))
    assert list(jev.asked[-1]["questions"]) == ["c1.backed"]


def test_an_answer_under_a_card_is_asked_whether_it_leaves_a_term_of_the_cards_first_line_unexplained_and_holds_at_yes(jev: Jev) -> None:
    card = {"title": "Mac chats' rows in the API catch up from a saved mark", "asked": "Why does the API need to know any of this?"}
    output = handled(CLAIM["claim"]) | {"thread_card": card}
    jev.picks("backed", 0.9)
    assert settled.post_verdict(output) == PASSES
    assert (jev.asked[-1]["state"]["card"], jev.asked[-1]["questions"]["card"]) == (card, settled.CARD)
    jev.breaks("card", "", 0.9)
    assert settled.post_verdict(output) == {
        "verdict": "hold",
        "note": f'Jev held back this post: it leaves a term of the card "{card["title"]}", which his question turns on, unexplained: say in a clause what each term of it your answer needs is and why the change needs it. So fix each, then call say again.',
    }
    settled.post_verdict(handled(CLAIM["claim"]))
    assert "card" not in jev.asked[-1]["questions"]


def test_jev_reads_the_typed_words_beside_slacks_and_asks_no_fact_of_a_sentence_that_is_a_reference_alone(jev: Jev) -> None:
    card = "{card:demo:j1-1}"
    drawn = "<https://example.slack.com/archives/D1/p1|Release the inbox fix to staging now?>"
    post = {"text": f"{CLAIM['claim']}\n{drawn}", "written": f"{CLAIM['claim']}\n{card}", "claims": [CLAIM]}
    jev.picks("backed", 0.9)
    assert settled.post_verdict({"post": post, "reads": [READ], "his_words": []}) == {"verdict": "pass", "note": ""}
    assert jev.asked[-1]["state"]["post"] == {"text": post["text"], "written": post["written"]}
    assert list(jev.asked[-1]["questions"]) == ["c1.backed"]


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


def test_a_read_and_a_card_too_long_for_one_request_together_reach_jev_whole_in_two_and_a_read_no_request_holds_is_named_by_its_size(jev: Jev) -> None:
    query = "400  rows, err := r.postgres.Query(ctx, selectInbox, agentID)"

    def lines(count: int) -> str:
        return "\n".join([*(f"{line}  ok := cache.Hit(key)" for line in range(1, count)), query])

    output = handled(CLAIM["claim"], [READ | {"quote": f"{CALL_READ}\n{lines(250)}"}])
    assert settled.unfit(output)[1] == []
    jev.picks("backed", 0.9)
    assert settled.post_verdict(output) == PASSES
    [request] = jev.asked
    assert request["state"]["reads"]["read:1"]["result"] == lines(250)
    card = {"title": "Mac chats' rows in the API catch up from a saved mark", "asked": "Why does the API need to know any of this?"}
    filled = handled(CLAIM["claim"], [READ | {"quote": f"{CALL_READ}\n{lines(330)}"}]) | {"thread_card": card}
    assert settled.unfit(filled)[1] == []
    jev.asked.clear()
    assert settled.post_verdict(filled) == PASSES
    assert sorted(list(request["questions"]) for request in jev.asked) == [["c1.backed"], ["card"]]
    assert all(settled.size(request["state"], request["questions"]) <= settled.REQUEST_CHARS for request in jev.asked)
    post, parts = settled.unfit(handled(CLAIM["claim"], [READ | {"quote": f"{CALL_READ}\n{lines(500)}"}]))
    assert (post, [label for label, _ in parts]) == (settled.size({"post": {"text": CLAIM["claim"], "written": CLAIM["claim"]}}, {}), ['read:1 "cloudrunner/inbox.go" beside the claim naming it'])
    assert parts[0][1] > settled.REQUEST_CHARS


def test_a_reports_whole_body_beside_a_narrow_read_reaches_jev_whole_in_a_request_of_its_own_and_only_a_body_no_request_holds_is_named(jev: Jev) -> None:
    def body(count: int) -> str:
        return "\n".join(f"716852 line {line}: the five old-design builders stopped" for line in range(1, count))

    wide = {"id": "record:716852", "read": "716852", "source": "the report it carries", "quote": f"cci show 716852\n{body(100)}"}
    narrow = READ | {"id": "read:2", "quote": f"{CALL_READ}\n" + "\n".join(f"{line}  ok := cache.Hit(key)" for line in range(1, 200))}
    claims = [*({"claim": f"builder {n} stopped", "read": "record:716852"} for n in range(5)), {"claim": CLAIM["claim"], "read": "read:2"}]
    text = " ".join(claim["claim"] + "." for claim in claims)
    output = handled(text, [wide, narrow], claims)
    assert settled.unfit(output)[1] == []
    jev.picks("backed", 0.9)
    assert settled.post_verdict(output) == PASSES
    assert sorted(sorted(request["state"]["reads"]) for request in jev.asked) == [["read:2"], ["record:716852"]]
    assert next(request["state"]["reads"]["record:716852"]["result"] for request in jev.asked if "record:716852" in request["state"]["reads"]) == body(100)
    longer = wide | {"quote": f"cci show 716852\n{body(220)}"}
    assert [label for label, _ in settled.unfit(handled(text, [longer, narrow], claims))[1]] == ['record:716852 "716852" beside the 5 claims naming it']
