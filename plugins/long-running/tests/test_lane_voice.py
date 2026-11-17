import io
import json

import pytest
from lane import store, voice
from lane import words as words_module


def test_the_sender_queues_at_the_lane_channel_root_never_its_old_home_thread_and_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = {"id": "run-page", "channel": "C0RUNPAGE", "thread": "1794863488.099589", "prs": []}
    sent = []

    def answered(request, timeout):
        sent.append((request.full_url, json.loads(request.data), timeout))
        return io.StringIO(json.dumps({"id": "a1b2c3"}))

    monkeypatch.setattr(voice.urllib.request, "urlopen", answered)
    assert voice.send("demo", lane, "Right, v3 doesn't need the conversion.") == "QUEUED a1b2c3"
    assert sent == [
        (
            voice.VOICE_URL,
            {
                "lane": "run-page",
                "drive": "demo",
                "channel_id": "C0RUNPAGE",
                "thread_ts": "",
                "text": "Right, v3 doesn't need the conversion.",
                "words": "Right, v3 doesn't need the conversion.",
                "prs": [],
                "about": [],
                "rules": str(voice.ROLE_PAGE),
                "asked": "",
            },
            voice.ENQUEUE_SECONDS,
        )
    ]


@pytest.mark.parametrize(("named", "owner"), [("demo/run-page", ("demo", "run-page")), (None, None)])
def test_only_a_session_started_as_a_lane_owner_takes_the_fast_path(monkeypatch: pytest.MonkeyPatch, named: str | None, owner: tuple[str, str] | None) -> None:
    if named:
        monkeypatch.setenv(voice.LANE_ENV, named)
    else:
        monkeypatch.delenv(voice.LANE_ENV, raising=False)
    assert voice.own_lane() == owner


PRS = [{"repo": "Forge-AI/monorepo", "number": 53430, "words": "reads old journals"}, {"repo": "Forge-AI/monorepo", "number": 53305}, {"repo": "yasyf/cc-skills", "number": 53305}]


@pytest.mark.parametrize(
    ("typed", "drawn"),
    [
        ("Reworking {pr:53430|v3 reads}.", "Reworking <https://github.com/Forge-AI/monorepo/pull/53430|#53430 v3 reads>."),
        ("{pr:53430} lands first", "<https://github.com/Forge-AI/monorepo/pull/53430|#53430 reads old journals> lands first"),
        ("{pr:yasyf/cc-skills#53305|the hook}", "<https://github.com/yasyf/cc-skills/pull/53305|#53305 the hook>"),
        ("{pr:53305} is two PRs", "{pr:53305} is two PRs"),
        ("{pr:999} is none of the lane's", "{pr:999} is none of the lane's"),
        ("{pr:Forge-AI/monorepo#56399|two new tasks columns}", "<https://github.com/Forge-AI/monorepo/pull/56399|#56399 two new tasks columns>"),
        (
            "Your change is <https://github.com/Forge-AI/monorepo/pull/57961|#57961 roots start subagents unforked> (https://github.com/Forge-AI/monorepo/pull/57961). CI is red.",
            "Your change is <https://github.com/Forge-AI/monorepo/pull/57961|#57961 roots start subagents unforked>. CI is red.",
        ),
        ("Merged <https://github.com/Forge-AI/monorepo/pull/53430|#53430>.", "Merged <https://github.com/Forge-AI/monorepo/pull/53430|#53430 reads old journals>."),
        ("Merged #53430, not #999.", "Merged <https://github.com/Forge-AI/monorepo/pull/53430|#53430 reads old journals>, not #999."),
        ("<https://x.example/a|see #53430>", "<https://x.example/a|see #53430>"),
    ],
)
def test_a_pr_the_lane_holds_draws_as_its_number_and_words(typed: str, drawn: str) -> None:
    assert voice.linked(typed, PRS) == drawn


def test_a_question_carries_its_options_to_the_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []
    monkeypatch.setattr(voice.urllib.request, "urlopen", lambda request, timeout: sent.append(json.loads(request.data)) or io.StringIO(json.dumps({"id": "q1"})))
    assert voice.send("demo", {"id": "iris", "channel": "C1", "prs": []}, "Hotfix #57810 now, or wait?", None, ["Hotfix", "Wait"]) == "QUEUED q1"
    assert sent[0]["options"] == ["Hotfix", "Wait"]


def test_the_sender_names_each_linked_pr_once_with_its_state() -> None:
    text = "<https://github.com/Forge-AI/monorepo/pull/57894|#57894 docs> needs your Approve; <https://github.com/Forge-AI/monorepo/pull/57894|#57894 docs> again, <https://github.com/Forge-AI/monorepo/pull/57908|#57908 release tool>."
    seen = {"Forge-AI/monorepo#57894": {"state": "OPEN"}, "Forge-AI/monorepo#57908": {"state": "MERGED"}}
    assert voice.named_prs(text, seen) == [{"repo": "Forge-AI/monorepo", "number": 57894, "state": "OPEN", "head": "", "approved": False}, {"repo": "Forge-AI/monorepo", "number": 57908, "state": "MERGED", "head": "", "approved": False}]


def test_importing_voice_makes_it_lane_say_s_sender() -> None:
    from lane import say

    assert say.SENDER is voice.send


def test_his_message_reaches_its_lane_by_thread_and_every_lane_in_the_channel_when_unclear(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import events, store

    root = tmp_path / "lanes"
    (root / "demo").mkdir(parents=True)
    lanes = {"run-page": {"channel": "C0RUN", "thread": None}, "v3-a": {"channel": "C0SHARED", "thread": "1.1"}, "v3-b": {"channel": "C0SHARED", "thread": "2.2"}}
    monkeypatch.setattr(store, "root", lambda: root)
    monkeypatch.setattr(store, "load_all", lambda drive: lanes)
    heard = []
    monkeypatch.setattr(events, "message", lambda drive, lane_id, words, link, ts: heard.append((lane_id, ts)) or f"{lane_id}: 1 new")
    assert voice.heard("C0RUN", None, "make this a prio lane", None, "9.9") == "run-page: 1 new"
    assert voice.heard("C0SHARED", "2.2", 'tapped "Approve"', None, "9.8") == "v3-b: 1 new"
    assert voice.heard("C0SHARED", None, "both of these", None, "9.7") == "v3-a: 1 new; v3-b: 1 new"
    assert voice.heard("C0SHARED", "3.3", "an older thread", None, "9.6") == "v3-a: 1 new; v3-b: 1 new"
    assert voice.heard("D0DM", None, 'tapped "Approve"', "https://x/p1", "9.5") == "no lane holds D0DM"
    assert heard == [("run-page", "9.9"), ("v3-b", "9.8"), ("v3-a", "9.7"), ("v3-b", "9.7"), ("v3-a", "9.6"), ("v3-b", "9.6")]


ASK = {"ts": "1794870764.063919", "link": "https://x.slack.com/archives/C1/p1794870764063919", "replied": "QUEUED a1"}
THREADED = {"ts": "1794871000.000100", "link": "https://x.slack.com/archives/C1/p1794871000000100?thread_ts=1794851267.832189&cid=C1"}
DM_ASK = {"ts": "1794870765.000001", "link": "https://x.slack.com/archives/D9/p1794870765000001"}


@pytest.mark.parametrize(
    ("asks", "about", "thread"),
    [
        ([], None, ""),
        ([ASK], None, "1794870764.063919"),
        ([ASK, THREADED], None, ""),
        ([ASK, THREADED], "1794870764.063919", "1794870764.063919"),
        ([ASK, THREADED], "1794871000.000100", "1794851267.832189"),
        ([ASK | {"done": "2026-11-16T23:50:00Z"}, THREADED], None, "1794851267.832189"),
        ([ASK | {"done": "2026-11-16T23:50:00Z"}], None, ""),
        ([DM_ASK], None, ""),
        ([DM_ASK], "1794870765.000001", ""),
    ],
)
def test_a_post_goes_in_the_thread_of_the_ask_it_names_or_the_one_open_ask_and_otherwise_at_the_root(asks: list[dict], about: str | None, thread: str) -> None:
    assert voice.asked_thread({"id": "run-page", "channel": "C1", "asks": asks}, about) == thread


def test_a_post_naming_an_ask_the_lane_never_heard_is_refused_before_it_goes_out() -> None:
    with pytest.raises(store.Refused, match="--re 9.9 names no ask"):
        voice.asked_thread({"id": "run-page", "channel": "C1", "asks": [ASK]}, "9.9")


@pytest.mark.parametrize(
    ("title", "words"),
    [
        ("Fix: litestream-next takes no snapshot of a forked child at its base TXID, now on ssql-litestream", "litestream-next takes no snapshot of a forked child at its base TXID, now on ssql-litestream"),
        ("feat(cc-slack): SandDB reads its own bucket", "SandDB reads its own bucket"),
        ("✨ New: migration gates as four simple rules", "migration gates as four simple rules"),
        ("⚡️ run_tallies reads each step's kept page row", "run_tallies reads each step's kept page row"),
        (":fire: delete today's run page", "delete today's run page"),
        (":recycle: Cleanup", ""),
        ("atablemovedeletesandcopiesnorowatoneroworahundredthousand now waits", "atablemovedeletesandcopiesnorowatoneroworahundredthousand now waits"),
        ("♻️ Refactor", ""),
        (":sparkles: New: migration gates as four simple rules", "migration gates as four simple rules"),
        ("New: a sync ends each LTX file", "a sync ends each LTX file"),
    ],
)
def test_a_title_reads_as_a_few_words(title: str, words: str) -> None:
    assert words_module.short(title) == words


def test_a_lane_pr_with_no_words_takes_them_from_its_title() -> None:
    rows = [{"repo": "Forge-AI/monorepo", "number": 57630}, {"repo": "Forge-AI/monorepo", "number": 1, "words": "own words"}]
    seen = {"Forge-AI/monorepo#57630": {"title": "Fix: sandbox cluster access for the admin and break-glass roles"}, "Forge-AI/monorepo#1": {"title": "ignored"}, "Forge-AI/monorepo#57908": {"title": "Fix: release-tool promotions run again"}}
    assert voice.linked("Merged #57630, {pr:1} and #57908.", voice.titled(rows, seen)) == (
        "Merged <https://github.com/Forge-AI/monorepo/pull/57630|#57630 sandbox cluster access for the admin and break-glass roles>,"
        " <https://github.com/Forge-AI/monorepo/pull/1|#1 own words> and <https://github.com/Forge-AI/monorepo/pull/57908|#57908 release-tool promotions run again>."
    )


POSTED = {"at": "2026-11-16T15:16:30.177-08:00", "options": [], "prs": [{"repo": "Forge-AI/monorepo", "number": 57894, "state": "", "head": ""}]}


@pytest.mark.parametrize(
    ("post", "lane", "now", "lines", "stale"),
    [
        (POSTED, {"prs": []}, {"state": "MERGED", "merged": True, "merged_at": "2026-11-16T23:18:05Z", "head": "a" * 40}, ["✅ #57894 merged 3:18 PM"], []),
        (POSTED, {"prs": []}, {"state": "MERGED", "merged": True, "merged_at": "2026-11-16T23:10:00Z", "head": "a" * 40}, [], []),
        (POSTED | {"prs": [POSTED["prs"][0] | {"state": "OPEN", "head": "b" * 40}]}, {"prs": []}, {"state": "OPEN", "merged": False, "head": "a" * 40}, [], ["#57894 has a new head, aaaaaaa, since it read bbbbbbb"]),
        (POSTED | {"options": ["Approve #57894", "Hold #57894"]}, {"prs": [{"repo": "Forge-AI/monorepo", "number": 57894, "approved_head": "a" * 40}]}, {"state": "OPEN", "merged": False, "head": "a" * 40}, [voice.RETIRED], []),
        (POSTED | {"options": ["Approve #57894", "Hold #57894"]}, {"prs": [{"repo": "Forge-AI/monorepo", "number": 57894, "held": "his Hold"}]}, {"state": "OPEN", "merged": False, "head": "a" * 40}, ["⏸️ #57894 held"], []),
    ],
)
def test_a_post_takes_a_state_line_when_its_pr_settles_and_tells_its_owner_when_its_head_moves(post: dict, lane: dict, now: dict, lines: list[str], stale: list[str]) -> None:
    assert voice.changes(post, lane, {("Forge-AI/monorepo", 57894): now}, {}) == (lines, stale)


def test_a_post_follows_the_card_ask_and_release_it_is_about() -> None:
    post = {"at": "2026-11-16T16:30:38-08:00", "prs": [], "about": [{"kind": "card", "ts": "1794877409.837249", "answer": "Wait for the regular release"}, {"kind": "ask", "ts": "9.1"}, {"kind": "release", "ref": "r1"}]}
    lane = {"prs": [], "asks": [{"ts": "9.1", "done": "2026-11-16T17:30:00Z"}]}
    assert voice.changes(post, lane, {}, {"r1": {"name": "release 18320", "state": "live"}}) == (["✅ answered: Wait for the regular release", "✅ done", "🚀 release 18320 live"], [])


def test_a_post_names_the_cards_of_its_channel_and_the_subjects_its_owner_names() -> None:
    lane = {"id": "iris", "channel": "C1"}
    text = "See <https://x.slack.com/archives/C1/p1794877409837249|the card> and <https://x.slack.com/archives/C9/p1794877409837250|elsewhere>."
    assert voice.subjects(lane, text, "9.1", ["release:r1"]) == [{"kind": "ask", "ts": "9.1"}, {"kind": "card", "ts": "1794877409.837249"}, {"kind": "release", "ref": "r1"}]
    with pytest.raises(store.Refused, match="release:<ref> or card:<ts>"):
        voice.subjects(lane, text, None, ["deploy:x"])


def test_a_pr_he_approved_at_its_head_reads_approved_and_one_at_an_older_head_does_not() -> None:
    text = "<https://github.com/Forge-AI/monorepo/pull/53305|#53305 pages calls>"
    seen = {"Forge-AI/monorepo#53305": {"state": "OPEN", "head": "a" * 40}}
    assert voice.named_prs(text, seen, [{"repo": "Forge-AI/monorepo", "number": 53305, "approved_head": "a" * 40}])[0]["approved"] is True
    assert voice.named_prs(text, seen, [{"repo": "Forge-AI/monorepo", "number": 53305, "approved_head": "b" * 40}])[0]["approved"] is False


def test_an_edit_names_its_post_by_ts_or_permalink() -> None:
    assert voice.edit_ts("https://x.slack.com/archives/C1/p1794870990408269?thread_ts=1.1") == "1794870990.408269"
    assert voice.edit_ts("1794870990.408269") == "1794870990.408269"
    assert voice.edit_ts(None) is None


def test_a_live_card_asking_about_work_he_already_approved_is_retired_as_landing() -> None:
    card = {"at": "2026-11-16T17:07:41-08:00", "options": ["Merge v3 now", "Hold for now"], "after": "Run page v3 is ready to merge: tap and they go in now.", "prs": [{"repo": "Forge-AI/monorepo", "number": n, "state": "", "head": ""} for n in (53305, 53810, 54428)], "about": []}
    lane = {"prs": [{"repo": "Forge-AI/monorepo", "number": 53305, "approved_head": "a" * 40}, {"repo": "Forge-AI/monorepo", "number": 53810, "approved_head": "b" * 40}, {"repo": "Forge-AI/monorepo", "number": 54428, "held": "his Hold"}]}
    now = {("Forge-AI/monorepo", 53305): {"state": "OPEN", "merged": False, "head": "a" * 40}, ("Forge-AI/monorepo", 53810): {"state": "OPEN", "merged": False, "head": "b" * 40}, ("Forge-AI/monorepo", 54428): {"state": "OPEN", "merged": False, "head": "c" * 40}}
    assert voice.changes(card, lane, now, {}) == ([voice.RETIRED], [])
    lane["prs"][1]["approved_head"] = "old"
    assert voice.changes(card, lane, now, {}) == ([], [])


def test_a_pr_whose_title_is_one_generic_word_takes_its_bodys_first_line(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(store, "root", lambda: tmp_path)
    calls = []

    def viewed(argv, **_):
        calls.append(argv)
        return __import__("subprocess").CompletedProcess(argv, 0, "## Summary\n\n**Drops the old card queue** and its tests.\n", "")

    monkeypatch.setattr(words_module.subprocess, "run", viewed)
    assert words_module.pr_words("Forge-AI/monorepo", 57649, ":recycle: Cleanup") == "Drops the old card queue and its tests."
    assert words_module.pr_words("Forge-AI/monorepo", 57649, ":recycle: Cleanup") == "Drops the old card queue and its tests."
    assert len(calls) == 1
    assert words_module.pr_words("Forge-AI/monorepo", 1, "♻️ Drop the card queue") == "Drop the card queue"


def test_the_sender_hands_the_owner_the_voices_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice.urllib.request, "urlopen", lambda request, timeout: io.StringIO(json.dumps({"id": "g1", "warning": "warning, posted: it names work nobody is doing yet"})))
    assert voice.send("demo", {"id": "cloud", "channel": "C1", "prs": []}, "No job carries these yet.") == "QUEUED g1; warning, posted: it names work nobody is doing yet"


def test_his_rulings_ride_a_question_and_its_words_go_as_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []
    monkeypatch.setattr(voice.urllib.request, "urlopen", lambda request, timeout: sent.append(json.loads(request.data)) or io.StringIO(json.dumps({"id": "c1"})))
    monkeypatch.setattr(voice.store, "shared_rulings", lambda drive: [{"at": "2026-11-16T16:58:00-08:00", "binds": "no staging hold"}])
    lane = {"id": "blobstore", "channel": "C1", "prs": [], "rulings": [{"words": "ship the pool", "at": "2026-11-16T12:00:00-08:00"}]}
    voice.send("demo", lane, "Ship the pool now?", None, ["Ship it", "Wait"])
    assert ("card" in sent[0], sent[0]["text"], sent[0]["options"]) == (False, "Ship the pool now?", ["Ship it", "Wait"])
    assert sent[0]["rulings"] == [{"at": "4:58 PM", "binds": "no staging hold"}, {"at": "12:00 PM", "binds": "ship the pool"}]


def test_a_post_the_voice_never_sent_tells_its_owner_once(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    told = []
    monkeypatch.setattr(voice.verbs, "handover", lambda drive, lane, text, by: told.append((lane, text)))
    monkeypatch.setattr(voice.verbs, "wake", lambda drive, lane, text, if_waiting: told.append((lane, "woke")))
    row = {"id": "a1", "lane": "releases", "at": "2026-11-16T15:56:41.000-08:00", "error": "channel_not_found", "before": "The everything-but-SandSQL release passed on staging."}
    lanes = {"releases": {"state": store.WAITING}}
    voice.unsent("d", lanes, [row, row | {"lane": "gone"}])
    voice.unsent("d", lanes, [row])
    assert told[0][0] == "releases" and "Your post of 3:56 PM never reached him (channel_not_found)" in told[0][1] and "release passed on staging" in told[0][1]
    assert told[1:] == [("releases", "woke")]



def test_his_words_no_lane_holds_go_to_the_lane_jev_picks_in_the_call_and_only_reading_starts_a_session_which_later_words_join(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import events, intake

    root = tmp_path / "lanes"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / store.LIVE).touch()
    lanes = {"run-page": {"channel": "C0RUN", "thread": None, "title": "Run page v3", "says": "Run page reads v3 journals", "state": store.WAITING}}
    monkeypatch.setattr(store, "root", lambda: root)
    monkeypatch.setattr(store, "load_all", lambda drive: lanes)
    monkeypatch.setattr(store, "load", lambda drive, lane_id: lanes[lane_id])
    monkeypatch.setattr(store, "config", lambda drive: {})
    picks = {"where is the run page at": "run-page", "thanks": intake.CHATTER}
    monkeypatch.setattr(intake, "route", lambda drive, words: picks.get(words, intake.READ))
    handed, replied, started = [], [], []
    monkeypatch.setattr(events, "message", lambda drive, lane_id, words, link, ts: handed.append((lane_id, ts)) or f"{lane_id}: 1 new")
    monkeypatch.setattr(intake, "reply", lambda drive, channel, thread, text, options=None: replied.append((channel, thread, text)))
    monkeypatch.setattr(intake, "prompt", lambda drive, channel, thread, words, link, ts: words)
    monkeypatch.setattr(intake.session, "argv", lambda config, role, checkout, own, head, tail: [role, *tail])
    monkeypatch.setattr(intake.subprocess, "Popen", lambda argv, **kwargs: started.append(argv[-1]))
    link = "https://x.slack.com/archives/D0DM/p1"
    assert voice.heard("D0DM", None, "where is the run page at", link, "9.1") == "intake of 9.1: run-page: 1 new"
    assert voice.heard("D0DM", None, "where is the run page at", link, "9.1") == "intake of 9.1: already heard"
    assert replied == [("D0DM", "9.1", "<https://x.slack.com/archives/C0RUN|Run page v3> has this: Run page reads v3 journals")]
    assert voice.heard("D0DM", None, "thanks", link, "9.2") == "intake of 9.2: chatter, nothing to do"
    assert voice.heard("D0DM", None, "open a lane for the billing export", link, "9.3") == "intake of 9.3: an intake session started"
    assert voice.heard("D0DM", None, "and give it the invoices too", link, "9.4").startswith("intake of 9.4: joined the intake that started at ")
    assert voice.heard("D0DM", None, 'tapped "Approve"', None, "9.5") == "intake of 9.5: a tap on no question of the intake's"
    assert (handed, started) == ([("run-page", "9.1")], ["open a lane for the billing export"])
    assert "and give it the invoices too" in (root / "demo" / intake.INTAKES / "D0DM.jsonl").read_text()


def test_at_the_cap_the_intake_asks_which_lane_to_park_least_urgent_first_and_his_tap_parks_it_and_opens_in_the_call(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import intake, rank, verbs

    root = tmp_path / "lanes"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / store.LIVE).touch()
    monkeypatch.setattr(store, "root", lambda: root)
    monkeypatch.setattr(store, "load_all", lambda drive: {"a": {"state": store.RUNNING, "channel": "C1"}, "b": {"state": store.WAITING, "channel": "C2"}, "c": {"state": store.PARKED, "channel": "C3"}})
    monkeypatch.setattr(rank, "last", lambda drive: {"order": ["a", "b"]})
    replied, opened = [], []
    monkeypatch.setattr(intake, "reply", lambda drive, channel, thread, text, options=None: replied.append((thread, options)) or "QUEUED q1")
    monkeypatch.setattr(verbs, "open_lane", lambda drive, **kwargs: opened.append(kwargs) or {"id": kwargs["lane_id"], "title": kwargs["title"]})
    opening = {"lane_id": "billing", "title": "Billing export", "words": "open a lane for the billing export", "link": "https://x.slack.com/archives/D0DM/p1", "ts": "9.1", "worktree": "/w"}
    assert intake.ask_park("demo", "D0DM", "9.1", "Billing export needs a lane; one must park for it.", opening) == "QUEUED q1"
    assert replied == [("9.1", ["Park b", "Park a"])] and not opened
    assert voice.heard("D0DM", "9.1", 'tapped "Park b"', None, "9.2") == "intake of 9.2: parked b, opened billing"
    assert opened == [opening | {"parks": "b", "park_words": 'tapped "Park b" (9.2)'}] and intake.pending("demo") == []
