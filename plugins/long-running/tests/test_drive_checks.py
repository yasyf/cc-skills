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
    assert settled.refusal_verdict(RULE, CALL, [FINDING]) == verdict
    [request] = jev.asked
    assert request == {"state": {"rule": RULE, "call": CALL}, "questions": {"protects": settled.PROTECTS}}


def test_a_refusal_jev_gives_no_verdict_on_only_warns_saying_the_call_goes_ahead_and_naming_what_its_check_found(jev: Jev) -> None:
    jev.answer = None
    assert settled.refusal_verdict(RULE, CALL, [FINDING]) == {
        "verdict": "warn",
        "note": f"This call goes ahead: Jev gave no verdict (no jev decision within 20s) on whether it is what the refusal's rule protects against, so what the check found only warns:\n1. {FINDING['found']}",
    }


def test_a_handlers_claim_goes_to_jev_beside_its_reads_whole_output_and_goes_out_with_a_warning_under_backs_and_stands_at_it(jev: Jev) -> None:
    output = handled(CLAIM["claim"])
    warned = settled.post_verdict(output)
    assert warned == {
        "verdict": "warn",
        "note": f'{settled.GOES_OUT} Jev finds claim "{CLAIM["claim"]}" does not stand on its read: reword the post in place.',
    }
    assert jev.asked[-1] == {
        "state": {
            "claims": {"c1": {"claim": CLAIM["claim"]}},
            "reads": {"read:1": {"call": CALL_READ, "result": RESULT}},
        },
        "questions": {"c1.backed": settled.asking(settled.BACKED, read="read:1", claim="c1")},
    }
    jev.breaks("c1", "backed", settled.BACKS - 0.01)
    assert settled.post_verdict(output)["verdict"] == "warn"
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
    assert settled.post_verdict(handled(text)) == {"verdict": "warn", "note": f'{settled.GOES_OUT} Jev finds "The fix ships tonight." states a fact no claim holds whole: reword the post in place.'}


def test_a_sentence_one_claim_holds_in_part_is_asked_as_a_fact_until_the_claims_of_its_parts_hold_it_whole_links_and_drive_references_aside(jev: Jev) -> None:
    sentence = "The cloud's inbox reader queries Postgres straight, and the pod reads the bucket {pr:7}."
    jev.picks("backed", 0.9)
    settled.post_verdict(handled(sentence))
    assert list(jev.asked[-1]["questions"]) == ["c1.backed", "s0.fact"]
    parts = [CLAIM, {"claim": "and the pod reads the bucket", "read": "read:1"}]
    settled.post_verdict(handled(sentence, claims=parts))
    assert list(jev.asked[-1]["questions"]) == ["c1.backed", "c2.backed"]


def test_a_sentence_no_claim_holds_whole_goes_to_jev_beside_every_claim_so_a_lead_line_summing_them_up_is_weighed_against_them(jev: Jev) -> None:
    text = f"*Live: the inbox reader skips the cache.*\n{CLAIM['claim']}"
    settled.post_verdict(handled(text, [READ | {"quote": 'none: no call of the handler\'s own holds "cloudrunner/inbox.go"'}]))
    assert jev.asked == [
        {
            "state": {"post": {"sentences": {"0": "*Live: the inbox reader skips the cache.*"}}, "claims": {"c1": {"claim": CLAIM["claim"]}}},
            "questions": {"s0.fact": settled.asking(settled.FACT, index=0)},
        }
    ]


def test_a_claim_saying_only_every_no_or_never_of_a_set_is_asked_whether_its_read_reads_one_member_and_warns_at_yes(jev: Jev) -> None:
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
        "verdict": "warn",
        "note": f'{settled.GOES_OUT} Jev finds claim "{only["claim"]}" speaks for a whole set while its read reads one member of it: reword the post in place.',
    }
    jev.breaks("c1", "spans", 0.5)
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f'{settled.GOES_OUT} Jev is unsure of claim "{only["claim"]}" speaking for more of a set than its read reads: if one is wrong, reword the post in place.',
    }
    settled.post_verdict(handled(CLAIM["claim"]))
    assert list(jev.asked[-1]["questions"]) == ["c1.backed"]


def test_an_answer_under_a_card_is_asked_whether_it_leaves_a_term_of_the_cards_first_line_unexplained_and_warns_at_yes(jev: Jev) -> None:
    card = {"title": "Mac chats' rows in the API catch up from a saved mark", "asked": "Why does the API need to know any of this?"}
    output = handled(CLAIM["claim"]) | {"thread_card": card}
    jev.picks("backed", 0.9)
    assert settled.post_verdict(output) == PASSES
    assert (jev.asked[-1]["state"]["card"], jev.asked[-1]["questions"]["card"]) == (card, settled.CARD)
    jev.breaks("card", "", 0.9)
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f'{settled.GOES_OUT} Jev finds it leaves a term of the card "{card["title"]}", which his question turns on, unexplained: reword the post in place.',
    }
    settled.post_verdict(handled(CLAIM["claim"]))
    assert "card" not in jev.asked[-1]["questions"]


def test_a_post_in_a_thread_of_his_dm_holding_orders_warns_when_jev_reads_it_telling_of_the_whole_drives_work_unless_his_words_there_name_more(jev: Jev) -> None:
    scope = {"asked": "what else is waiting on me for this work", "strays": []}
    output = {"post": {"text": "Nothing else of the copy fix waits on you."}, "his_words": [], "thread_scope": scope}
    assert settled.post_verdict(output) == PASSES
    assert jev.asked[-1]["state"]["thread"] == {"asked": scope["asked"]}
    assert (jev.asked[-1]["questions"]["scope"], jev.asked[-1]["questions"]["wider"]) == (settled.SCOPE, settled.WIDER)
    jev.breaks("scope", "", 0.9)
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f"{settled.GOES_OUT} Jev finds it tells him of the whole drive's work, which his words in this thread never asked about: reword the post in place.",
    }
    jev.breaks("wider", "", 0.5)
    assert settled.post_verdict(output)["note"].startswith(f"{settled.GOES_OUT} Jev finds it tells him of the whole drive's work")
    jev.breaks("wider", "", 0.9)
    assert settled.post_verdict(output) == PASSES
    jev.ruled = {"scope": ("", 0.5)}
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f"{settled.GOES_OUT} Jev is unsure of whether it tells him of the whole drive's work: if one is wrong, reword the post in place.",
    }


@pytest.mark.parametrize(
    ("asked", "post", "rewrite"),
    [
        ("Answered, anything else we're waiting on me? Is everything merged and tested?", "143 question cards and 139 PR Approve cards are still open in your DM.", "The copy fix is merged and tested, and nothing else here waits on you."),
        (
            "what's left and what do you need from me",
            "139 question cards and 110 PR Approve taps still wait on you in the dashboard's queue. By your words 'You should surface them to me if I ask and they are blocking', the queue ranks these four first. The rest are in the dashboard's queue.",
            "Left here: the copy fix, which waits on your Approve of its PR.",
        ),
    ],
)
def test_jev_warns_of_a_post_counting_the_whole_drives_cards_in_a_works_thread_and_passes_one_scoped_to_that_work(jev: Jev, asked: str, post: str, rewrite: str) -> None:
    scope = {"asked": asked, "strays": []}
    jev.breaks("scope", "", 0.9)
    assert settled.post_verdict({"post": {"text": post}, "his_words": [], "thread_scope": scope})["note"].startswith(f"{settled.GOES_OUT} Jev finds it tells him of the whole drive's work")
    assert (jev.asked[-1]["state"]["post"]["written"], jev.asked[-1]["state"]["thread"]) == (post, {"asked": asked})
    jev.ruled = {}
    assert settled.post_verdict({"post": {"text": rewrite}, "his_words": [], "thread_scope": scope}) == PASSES


def test_a_post_in_a_works_thread_naming_another_orders_pr_or_card_goes_out_with_a_warning_unless_his_words_there_name_more(jev: Jev) -> None:
    strays = ["{pr:yasyf/cc-skills#8}", "{card:demo:j2-1}"]
    output = {"post": {"text": "Also waiting on you: {pr:yasyf/cc-skills#8} and {card:demo:j2-1}."}, "his_words": [], "thread_scope": {"asked": "what else is waiting on me for this work", "strays": strays}}
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f"{settled.GOES_OUT} Jev is unsure of whether {{pr:yasyf/cc-skills#8}}, {{card:demo:j2-1}}, which the thread's work as the drive draws it does not hold, belongs in this thread: if one is wrong, reword the post in place.",
    }
    assert "strays" not in jev.asked[-1]["state"]["thread"]
    jev.breaks("wider", "", 0.9)
    assert settled.post_verdict(output) == PASSES


def test_jev_reads_the_typed_words_once_and_asks_no_fact_of_a_sentence_that_is_a_reference_alone(jev: Jev) -> None:
    card = "{card:demo:j1-1}"
    drawn = "<https://example.slack.com/archives/D1/p1|Release the inbox fix to staging now?>"
    post = {"text": f"{CLAIM['claim']}\n{drawn}", "written": f"{CLAIM['claim']}\n{card}", "claims": [CLAIM]}
    jev.picks("backed", 0.9)
    assert settled.post_verdict({"post": post, "reads": [READ], "his_words": []}) == {"verdict": "pass", "note": ""}
    assert "post" not in jev.asked[-1]["state"]
    assert list(jev.asked[-1]["questions"]) == ["c1.backed"]


def test_a_finding_warns_unless_jev_reads_its_rule_never_meant_it_or_his_words_lift_it_and_jev_reads_his_words_beside_it(jev: Jev) -> None:
    reason = f"refused: {FINDING['found']}"
    output = {"post": {"text": "Waits on your Approve: the first PR."}, "findings": [FINDING], "his_words": [{"id": "w1", "quote": "land the first PR"}], "hookSpecificOutput": {"permissionDecisionReason": reason}}
    jev.breaks("f1", "holds", 0.9)
    found = {"verdict": "warn", "note": f"{settled.GOES_OUT} Jev finds {FINDING['found']}: reword the post in place."}
    assert settled.post_verdict(output) == found
    assert (jev.asked[-1]["state"]["findings"], jev.asked[-1]["state"]["his_words"], list(jev.asked[-1]["questions"])) == ({"f1": FINDING}, {"w1": "land the first PR"}, ["f1.holds", "f1.lifted"])
    jev.picks("f1", 0.9)
    jev.ruled = {}
    stands = {"verdict": "warn", "note": f"{settled.GOES_OUT} Jev read none of these findings' rules as holding against it:\n1. {FINDING['found']}"}
    assert settled.post_verdict(output) == stands
    jev.picks("lifted", 0.5)
    jev.breaks("f1", "holds", 0.1)
    assert settled.post_verdict(output) == stands
    jev.breaks("f1", "holds", 0.5)
    assert settled.post_verdict(output) == found
    jev.breaks("f1", "holds", 0.9)
    assert settled.post_verdict(output) == found


def test_a_post_that_goes_out_names_no_finding_as_refused_and_a_finding_jev_had_no_room_for_warns(jev: Jev) -> None:
    every = {"claim": "Every inbox reader queries Postgres straight.", "read": "read:1"}
    reason = f"refused: {FINDING['found']}"
    output = handled(every["claim"], claims=[every]) | {"findings": [FINDING], "hookSpecificOutput": {"permissionDecisionReason": reason}}
    jev.picks("backed", 0.9)
    jev.breaks("c1", "spans", 0.5)
    assert settled.post_verdict(output) == {
        "verdict": "warn",
        "note": f'{settled.GOES_OUT} Jev is unsure of claim "{every["claim"]}" speaking for more of a set than its read reads: if one is wrong, reword the post in place.',
    }
    unweighed = {"rule": FINDING["rule"], "found": "the first PR " * 1000}
    unroomed = settled.post_verdict(output | {"findings": [unweighed]})
    assert unroomed["verdict"] == "warn"
    assert f"which had no room in Jev's {settled.REQUEST_CHARS:,} characters beside his words" in unroomed["note"]


def test_a_post_jev_gives_no_verdict_on_goes_out_naming_what_the_checks_found_and_holds_only_a_claim_naming_a_read_never_made(jev: Jev) -> None:
    jev.answer = None
    reason = f"refused: {FINDING['found']}"
    finding = {"post": {"text": "Merged."}, "findings": [FINDING], "his_words": [], "hookSpecificOutput": {"permissionDecisionReason": reason}}
    assert settled.post_verdict(finding) == {
        "verdict": "warn",
        "note": f"{settled.GOES_OUT} Jev gave no verdict (no jev decision within 20s), so what the checks found only warns:\n1. {FINDING['found']}",
    }
    unmatched = handled(CLAIM["claim"], [READ | {"quote": 'none: no call of the handler\'s own holds "cloudrunner/inbox.go"'}]) | {"thread_card": {"title": "Mac chats", "asked": "Why?"}}
    assert settled.post_verdict(unmatched)["note"].startswith('Jev held back this post: claim "The cloud\'s inbox reader queries Postgres straight." names the read "read:1", which matches none of your calls')
    assert settled.post_verdict(handled(CLAIM["claim"]))["note"] == f"{settled.GOES_OUT} Jev gave no verdict (no jev decision within 20s) on whether each claim of it stands on the read it names."


def test_a_post_that_goes_out_says_so_first_and_names_each_finding_never_in_its_refusals_words(jev: Jev) -> None:
    long = {"rule": "A line in his thread is at most 60 words.", "found": "this post is 69 words, and a line in his thread is at most 60 words"}
    reason = f"refused, 2 ways; fix each, then post the fixed words once:\n1. {long['found']}\n2. {FINDING['found']}"
    output = {"post": {"text": "Merged."}, "findings": [long, FINDING], "his_words": [{"id": "w1", "quote": "land the first PR"}], "hookSpecificOutput": {"permissionDecisionReason": reason}}
    verdict = settled.post_verdict(output)
    assert verdict["verdict"] == "warn"
    assert verdict["note"].startswith(settled.GOES_OUT)
    assert "refused" not in verdict["note"]
    assert "fix each" not in verdict["note"]
    assert all(finding["found"] in verdict["note"] for finding in [long, FINDING])


def test_a_read_and_a_card_too_long_for_one_request_together_reach_jev_whole_in_two(jev: Jev) -> None:
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


def test_a_claim_naming_a_drive_pr_stands_on_the_drives_line_for_it_when_its_own_read_says_less_and_keeps_the_read_it_names(jev: Jev) -> None:
    owed = {"claim": "Its builder owes #56983's fix.", "read": "read:1"}
    call = "drive status Forge-AI/monorepo#56983"
    line = "Forge-AI/monorepo#56983 open, checks fail, review none, no tap needed; red, owed by its builder: its checks fail"
    output = handled(owed["claim"], [READ, {"id": "stage:1", "read": call, "source": "the drive's own line", "quote": f"{call}\n{line}", "claims": [1]}], [owed])
    assert settled.post_verdict(output)["verdict"] == "warn"
    assert jev.asked[-1] == {
        "state": {
            "claims": {"c1": {"claim": owed["claim"]}},
            "reads": {"read:1": {"call": CALL_READ, "result": RESULT}, "stage:1": {"call": call, "result": line}},
        },
        "questions": {"c1.backed": settled.asking(settled.BACKED, read="read:1", claim="c1"), "c1.backed.stage:1": settled.asking(settled.BACKED, read="stage:1", claim="c1")},
    }
    jev.breaks("c1", "backed.stage:1", settled.BACKS)
    assert settled.post_verdict(output) == PASSES


def test_a_read_too_long_for_one_request_reaches_jev_whole_in_parts_by_line_its_claim_standing_on_the_part_backing_it(jev: Jev) -> None:
    only = {"claim": "Only the cloud's inbox reader queries Postgres straight.", "read": "read:1"}
    result = "\n".join([*(f"{line}  ok := cache.Hit(key)" for line in range(1, 500)), "", "501  rows, err := r.postgres.Query(ctx, selectInbox, agentID)"])
    output = handled(only["claim"], [READ | {"quote": f"{CALL_READ}\n{result}"}], [only])
    assert settled.unfit(output)[1] == []
    assert settled.post_verdict(output)["verdict"] == "warn"
    parts = sorted(jev.asked, key=lambda request: int(next(iter(request["state"]["reads"]["read:1"]["result"]))))
    assert len(parts) > 1
    assert all(settled.size(request["state"], request["questions"]) <= settled.REQUEST_CHARS for request in parts)
    assert [line for request in parts for line in request["state"]["reads"]["read:1"]["result"].items()] == [(str(number), line) for number, line in enumerate(result.splitlines(), 1) if line]
    assert {(request["state"]["reads"]["read:1"]["call"], request["state"]["reads"]["read:1"]["lines"]) for request in parts} == {(CALL_READ, 501)}
    assert [request["questions"] for request in parts] == [
        {"c1.backed.1": settled.asking(settled.BACKED_LINES, read="read:1", claim="c1"), "c1.spans": settled.asking(settled.SPANS, read="read:1", claim="c1")},
        *({f"c1.backed.{index}": settled.asking(settled.BACKED_LINES, read="read:1", claim="c1")} for index in range(2, len(parts) + 1)),
    ]
    jev.breaks("c1", f"backed.{len(parts)}", settled.BACKS)
    assert settled.post_verdict(output) == PASSES
    long_line = handled(CLAIM["claim"], [READ | {"quote": f"{CALL_READ}\n{RESULT}\n{'ok := cache.Hit(key) ' * 500}"}])
    post, unfit = settled.unfit(long_line)
    assert (post, [label for label, _ in unfit]) == (settled.size({"post": {"written": CLAIM["claim"]}}, {}), ['read:1 "cloudrunner/inbox.go" beside the claim naming it'])
    assert unfit[0][1] > settled.REQUEST_CHARS


def test_a_reports_whole_body_beside_a_narrow_read_reaches_jev_whole_in_a_request_of_its_own_and_a_longer_body_in_parts(jev: Jev) -> None:
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
    longer = handled(text, [wide | {"quote": f"cci show 716852\n{body(220)}"}, narrow], claims)
    assert settled.unfit(longer)[1] == []
    jev.asked.clear()
    assert settled.post_verdict(longer) == PASSES
    parts = [request for request in jev.asked if "record:716852" in request["state"]["reads"]]
    assert len(parts) > 1
    assert sorted(line for request in parts for line in request["state"]["reads"]["record:716852"]["result"].values()) == sorted(body(220).splitlines())
    assert all({"c1", "c2", "c3", "c4", "c5"} <= set(request["state"]["claims"]) for request in parts)
