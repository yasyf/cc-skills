import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from drive import github, land, state, view

NOBODY = "nobody"
WATCH = "the watch"
LANDER = "the lander"
ORDER = "the session holding its order"
HIM = "him"
BUILDER = "its builder"
GRADERS = "Buildkite and forge-pr-reviewer"
ACCEPT_RULING = '2bb0472, "you have to accept the risk stop just pretendinf urblocked"'
UNLANDED_SILENT = "no outcome: its process ended before it said whether it queued anything"
WAKES_LOG = "wakes.log"
NAMED_HEAD = re.compile(r"\bat ([0-9a-f]{7,40})\b")
ASKS_HIM = ("held", "card")
OWN_STEP = ("uncarded", "landing", "unlanded", "held", "rework", "merged", github.FOLDED, "closed")
EVERY = {
    "uncarded": timedelta(minutes=30),
    "landing": timedelta(minutes=90),
    "unlanded": timedelta(minutes=30),
    "blocked": timedelta(minutes=60),
    "held": timedelta(minutes=30),
    "rework": timedelta(minutes=30),
    "red": timedelta(minutes=30),
    "waiting": timedelta(minutes=60),
    "grading": timedelta(minutes=60),
    "card": timedelta(minutes=30),
    "ready": timedelta(minutes=30),
    "author": timedelta(minutes=30),
}


@dataclass(frozen=True)
class Stage:
    name: str
    owner: str
    why: str


def of(data: dict, key: str, cards: dict, under: dict[str, str | None] | None = None, stacks: dict[str, tuple[str, ...]] | None = None) -> Stage:
    """What a PR is now and who owes its next step, the first rule that matches, the last matching every PR; the only place the drive says what a PR is, which the watch, the lander, his card, the dashboard and drive status read."""
    return behind(data, key, cards, own(data, key, cards), under, stacks)


def on_card(data: dict, key: str, cards: dict) -> Stage:
    """What his card on the PR says it is as that card draws: every rule of stage.of past the draw of his card."""
    return behind(data, key, cards, alone(data, key, cards))


def own(data: dict, key: str, cards: dict | None) -> Stage:
    """The PR's stage by its own row, whatever the rest of its landing stack reads: uncarded, owed by the watch, only while state.asks_tap finds his card would offer Approve, the check every draw of his card makes, so the watch owes no draw the draw refuses, and no card of his offers it at its head, by view.tap_card, the one read of his card every line naming it makes: none is drawn, the one open on it shows an older head, or cc-slack keeps the one drawn closed in place, either of which the watch redraws in place, so no stage names a card as his to tap that the say hook finds not open at its head; with his cards unread, whatever rule comes next."""
    pr = data["prs"][key]
    if not pr["rework"] and cards is not None and state.asks_tap(pr) and not view.tap_card(pr, cards):
        return Stage("uncarded", WATCH, uncarded(pr, cards))
    return alone(data, key, cards)


def uncarded(pr: dict, cards: dict) -> str:
    if shown := view.card_for(cards["pr_cards"], pr["url"]):
        return f"his card on it shows {view.shown_heads(shown, pr)} rather than its head {pr['head'][:7]}, so the watch redraws it in place"
    if view.standing_card(pr, cards):
        return "his card on it is closed, since Approve came off it while it waited on no tap, so the watch redraws it in place"
    return "his card on it is not drawn yet"


def alone(data: dict, key: str, cards: dict) -> Stage:
    """Every rule of own past the draw of his card; a PR its lander's try merged reads merged, owed by nobody, by land.merged_try, while the watch's last GitHub read still reads it open, so that try wakes no session; an unlanded PR whose own job's waits end at his open question cards, by asked(), reads blocked on him, naming them, so a lander's try made before that job waited wakes no session while he decides and reads unlanded again once he answers."""
    pr = data["prs"][key]
    if pr["state"] != "open":
        return Stage(pr["state"], NOBODY, github.fold_line(pr) if pr["state"] == github.FOLDED else f"it is {pr['state']}")
    if land.merged_try(pr):
        lander = pr["lander"]
        return Stage("merged", NOBODY, f"its lander's try at {lander['head'][:7]}, started {lander['at']}, ended {lander['outcome']}")
    if land.landing(pr):
        return Stage("landing", LANDER, landing_why(pr))
    if ended := unlanded(data, key, cards):
        if mine := asked(data, key, [pr["job"]], cards):
            return Stage("blocked", HIM, f"its builder {pr['job']} waits on {', '.join(fix_words(data, wait, cards) for wait in mine)}")
        return Stage("unlanded", ORDER, ended)
    if stops := stopped(pr):
        return Stage("held", HIM, stops)
    if step := owed(data, key):
        if waits := answered(data, key, step):
            return blocked(data, key, step, waits, cards)
        return Stage("rework", BUILDER, step) if pr["rework"] else flake_red(data, key, step, cards) or Stage("red", BUILDER, step)
    if holds := held(data, pr):
        return Stage("held", HIM, holds)
    if pending := grading(pr):
        return Stage("grading", GRADERS, pending)
    if state.ships_untapped(data, key):
        return waiting(data, key, cards) or Stage("ready", LANDER, f"its builder reported it and every check at {pr['head'][:7]} finished, so the lander merges it with --admin unless one fails on its own change")
    if (flaky := land.flaked(pr)) and not flaky["failed_checks"] and data["prs"][flaky["fix"]]["state"] == "open":
        return Stage(
            "waiting",
            mover(data, key, [flaky["fix"]], cards),
            f"Trunk ejected it from the merge queue at {pr['head'][:7]} on its flaky test {flaky['test']}; the lander requeues that commit once {flaky['fix']}, that test's fix, merges",
        )
    if state.cleared(pr):
        after = f"; the check against his words objects, for after the merge: {named}" if (named := state.against(pr)) else ""
        unread = f"; its lander's last try ended {lander['outcome']}" if (lander := pr["lander"]) and lander["head"] == pr["head"] and (lander["outcome"] or "").startswith(land.UNREAD) else ""
        return waiting(data, key, cards) or Stage("ready", LANDER, (f'green, its exempt class "{pr["exempt"]}" lands it' if state.exempted(pr) else f"green and approved by him at {pr['tap']['head'][:7]}{after}") + unread)
    if carded(pr):
        objection = f"; at {pr['head'][:7]} the check against his words objects: {named}" if (named := state.against(pr)) else ""
        return Stage("card", HIM, f"green, waits on his Approve{objection}")
    return Stage("author", BUILDER, "green; its author or its own builder lands it with no tap")


def waiting(data: dict, key: str, cards: dict) -> Stage | None:
    """The stage of a PR the lander would land now while a PR, job or release of the drive it waits on still holds, which every ready rule of alone reads first, so the lander lands none of them until the watch frees it: blocked, owed by him and naming each, while an end of those waits is his by asked(), so it wakes no one, else waiting, owed by the session mover() names; None once nothing it waits on holds."""
    if not (standing := view.holding(data, pr_waits(data, data["prs"][key]), cards)):
        return None
    if mine := asked(data, key, standing, cards):
        return Stage("blocked", HIM, f"it waits on {', '.join(fix_words(data, wait, cards) for wait in dict.fromkeys([*standing, *mine]))}")
    return Stage("waiting", mover(data, key, standing, cards), f"it waits on {', '.join(standing)}, and the watch frees it once each ends")


def pr_waits(data: dict, pr: dict) -> list[str]:
    """What a PR waits on: its own waits, and the original a pure port waits on until it merges or he approves it, by state.port_hold."""
    return [*pr["waits"], *filter(None, [state.port_hold(data, pr)])]


def asked(data: dict, key: str, waits: list[str], cards: dict | None) -> list[str]:
    """Each end of the PR's waits, by ends(), that only he moves."""
    return [end for end in ends(data, waits, cards, {key}) if his(data, end, cards)]


def ends(data: dict, waits: list[str], cards: dict | None, seen: set[str]) -> list[str]:
    """Where the waits that still hold lead, walked as stall.chain walks a chain: each job, each part of an order and each PR not waiting on his Approve to what holds it by onward(), to each wait holding on nothing further, never back to a PR walked, the one the walk starts from included, or to a release of one, which comes only once that PR lands, so waits that only come back to the PR end nowhere."""
    found = []
    for wait in view.holding(data, waits, cards):
        release = state.RELEASE_WAIT.fullmatch(wait)
        if wait in seen or release and release["pr"] in seen:
            continue
        seen.add(wait)
        held = onward(data, wait, cards) if wait in data["jobs"] or state.part_wait(data, wait) or wait in data["prs"] and not his(data, wait, cards) else []
        found += ends(data, held, cards, seen) if held else [wait]
    return found


def onward(data: dict, subject: str, cards: dict | None) -> list[str]:
    """What a job, part of an order or PR waits on that still holds, which ends() and stall.chain walk: a job waiting or built its waits and the cards of its own questions open on him, by view.open_cards, a part of an order each job carrying it whose work is still to come, by state.part_jobs, as a staging test part leads to its tester, and a PR its waits and his open cards holding it from landing, by land.pr_cards, since no wait names a card a job asked after its READY, PUSHED or WAITING or a builder asked mid-build, so a walk ends at his card wherever the drive keeps it; nothing for a job in any other state."""
    if job := data["jobs"].get(subject):
        waits = [*job["waits"], *view.open_cards(job)] if job["state"] in ("waiting", "built") else []
    elif part := state.part_wait(data, subject):
        waits = [job_id for job_id, _ in state.part_jobs(data, *part)]
    else:
        waits = [*pr_waits(data, data["prs"][subject]), *land.pr_cards(data, data["prs"][subject])]
    return view.holding(data, view.stored_waits(data, waits, cards), cards)


def mover(data: dict, key: str, waits: list[str], cards: dict | None) -> str:
    """Who moves the PR past the waits that still hold, at the ends ends() walks them to: him while one is his to move, by his(); else, at the first, the job at work there, a PR standing for the job answering for it, by owner_job(), so the stage's deadline wakes that job's lane; the watch for a builder slot on this Mac, which it gives; and the session holding the PR's order for a release, a staging soak commit, a process on this Mac, a part no job carries or waits that only come back to the PR."""
    found = ends(data, waits, cards, {key})
    if any(his(data, end, cards) for end in found):
        return HIM
    end = owner_job(data, found[0]) if found and found[0] in data["prs"] else next(iter(found), None)
    if end in data["jobs"]:
        return end
    return WATCH if end == state.SLOT_WAIT else ORDER


def his(data: dict, wait: str, cards: dict | None) -> bool:
    """Whether only he moves a wait: his question card, or a PR waiting on his Approve on its drawn card."""
    return state.card_wait(data, wait) or wait in data["prs"] and bool(view.tap_card(data["prs"][wait], cards))


def carded(pr: dict) -> bool:
    """Whether the PR waits on his card for its landing: it wants his tap and no exempt class lands it."""
    return pr["wants_tap"] and not state.exempted(pr)


def landing_why(pr: dict) -> str:
    lander = pr["lander"]
    if lander["outcome"] == land.QUEUED:
        flaky = pr["flaky"] if pr["flaky"] and pr["flaky"]["requeued"] and pr["flaky"]["head"] == lander["head"] else None
        requeued = f", requeued once {flaky['fix']}, the fix of its flaky test {flaky['test']}, merged" if flaky else ""
        return f"queued for merge at {lander['head'][:7]} since {lander['at']}{requeued}"
    if lander["outcome"]:
        return f"its lander's try at {lander['head'][:7]}, started {lander['at']}, {lander['outcome']}"
    return f"lander pid {lander['pid']} lands {lander['head'][:7]}, started {lander['at']}"


def unlanded(data: dict, key: str, cards: dict | None) -> str | None:
    """How a lander's try at the PR's head ended without landing it, or None when no try at this head stands: none ran on it, a wait of the PR, set before the try or after it, still leads somewhere by ends(), which waiting() reads, Trunk holds it, GitHub refused its merge for a conflict with its base or it found a check failing on its own change, which red() owes its builder, GitHub refused() the merge Trunk's queue took, so the lander lands it again one PR at a time, or ejected it on a flaky test whose fix it waits on, by ejection(), what let it run moved since, nothing clearing it now while it waits on his card, as after drive pr set --no-exempt --tap, or his tap made after the try started, or what the try waited on moved since, a PR below it that is cleared or merged now, a newer install's save that ended it before it queued anything, a hold of his that it or halt() stopped it on, which held() reads while it stands, or the lander's own failure, stack-enqueue ending with no answer on the PR or a gh write GitHub gave no answer to, by land.unanswered(), or a GitHub read that failed before it queued or merged anything, the landing clone's fetch of the default branch included, so the lander tries again."""
    pr = data["prs"][key]
    lander = pr["lander"]
    if not lander or lander["head"] != pr["head"] or ends(data, pr_waits(data, pr), cards, {key}) or land.running(pr) or land.refused(pr) or land.conflicted(pr) or land.own_red(pr):
        return None
    if carded(pr) and not state.cleared(pr) or pr["tap"] and state.parse_time(pr["tap"]["at"]) > state.parse_time(lander["at"]):
        return None
    if lander["outcome"] == land.QUEUED:
        return ejection(data, pr) if pr["ejected"] else None
    outcome = lander["outcome"] or UNLANDED_SILENT
    # TODO: delete once no PR's lander outcome predates land.unanswered(), such as yasyf/cc-slack#433's at 0317c21: those tries recorded a gh pr merge GitHub never answered as its refusal.
    if outcome.startswith(land.RETRIED) or outcome.startswith(f"not landed: {' '.join(land.MERGE)}") and github.UNANSWERED.search(outcome):
        return None
    if lander.get("below") and all(key in data["prs"] and (state.cleared(data["prs"][key]) or data["prs"][key]["state"] == "merged") for key in lander["below"]):
        return None
    return f"its lander's try at {lander['head'][:7]}, started {lander['at']}, ended {outcome}"


def ejection(data: dict, pr: dict) -> str | None:
    """How Trunk's ejection of the PR at its head stands: None while drive pr set --flaky names the flaky test it failed there and that test's fix is not closed unmerged, since the lander requeues that commit once the fix merges; final otherwise, naming that test and fix when they were named."""
    flaky = pr["flaky"] if pr["flaky"] and pr["flaky"]["head"] == pr["head"] else None
    if not flaky:
        return "Trunk ejected it from the merge queue"
    if flaky["requeued"]:
        return f"Trunk ejected it from the merge queue again after the lander requeued {pr['head'][:7]} once {flaky['fix']}, the fix of its flaky test {flaky['test']}, merged"
    if data["prs"][flaky["fix"]]["state"] == "closed":
        return f"Trunk ejected it from the merge queue on its flaky test {flaky['test']}, and {flaky['fix']}, that test's fix, closed unmerged"
    return None


def flake_red(data: dict, key: str, failing: str, cards: dict | None) -> Stage | None:
    """A PR red at its own head by the drive pr set --flaky record kept there while its checks failed: waiting on that test's fix, owed by the session moving that fix, by mover(), never its own builder, while the fix is open and every check failing at that head is one the record kept; red once the fix merged, so entering it wakes its builder once to restack onto the fix, since a rerun grades the merge its first run tested, or closed unmerged; None with no such record, or once a check the record lacks fails there."""
    pr = data["prs"][key]
    flaky = land.flaked(pr)
    if not flaky or pr["checks"] != "fail" or not set(pr["failed_checks"]) <= set(flaky["failed_checks"]):
        return None
    fix = data["prs"][flaky["fix"]]["state"]
    if fix == "open":
        return Stage("waiting", mover(data, key, [flaky["fix"]], cards), f"{failing}, on its flaky test {flaky['test']} outside its diff; its builder restacks it onto {flaky['fix']}, that test's fix, once it merges")
    then = "merged, so restack it onto that fix" if fix == "merged" else "closed unmerged, so fix that test at its cause"
    return Stage("red", BUILDER, f"{failing}; {flaky['fix']}, the fix of its flaky test {flaky['test']}, {then}")


def stopped(pr: dict) -> str:
    """What stops the PR whatever its checks read: his Hold, on which his card offers no Approve, or the hold label on GitHub, on which it still does, so a red PR under either wakes no builder."""
    if (pr["tap"] or {}).get("label") == "Hold":
        return f"held by his Hold of {pr['tap']['at']}"
    if pr["hold_label"]:
        return "held by the hold label on GitHub"
    return ""


def held(data: dict, pr: dict) -> str:
    """What of his holds the PR: what stops it, or a question card of his open on the work of the job its row names whose `Holds:` line holds it, or that the PR's `DESIGN:` line cites, by land.pr_cards; a red PR one of his cards holds reads red, so its builder keeps it green while he decides."""
    if stops := stopped(pr):
        return stops
    if asking := land.pr_cards(data, pr):
        return f"his cards {', '.join(asking)} on its work are open"
    return ""


def red(data: dict, key: str) -> str:
    """What its builder must fix at its head: failing checks, those of a yasyf/* PR its builder reported only once its lander found one failing on its own change, by land.own_red(), while one the default branch fails too merges, a conflict GitHub reads or refused the lander's merge for, named as conflicts_with() names it, with the restack that clears one in a native GitHub stack, a draft, changes a reviewer asked for, or, in a repository whose rules require an approving review before merging, a forge-pr-reviewer that finished on a green head and approved nothing, which holds it until each finding is fixed or its risk accepted."""
    pr = data["prs"][key]
    head = pr["head"][:7]
    if pr["checks"] == "fail" and land.own_red(pr):
        return pr["lander"]["outcome"].removeprefix("not landed: ")
    if pr["checks"] == "fail" and not state.ships_untapped(data, key):
        return f"its checks fail at {head}: {', '.join(pr['failed_checks']) or 'see its checks'}"
    if pr["conflict"] or land.conflicted(pr):
        restack = f": restack the whole stack onto {pr['trunk']} with ccx vcs stack rebase" if pr["trunk"] else ""
        return f"it conflicts with {conflicts_with(pr)}{restack}"
    if pr["draft"]:
        return "it is a draft"
    if pr["review"] == "changes":
        return f"a reviewer asked for changes at {head}"
    if pr["reviews_required"] and not state.merges_untapped(key) and pr["checks"] == "pass" and pr["review"] == "none":
        return (
            f"forge-pr-reviewer approved nothing at {head}: answer each of its findings now, with a fix or with a reply accepting its risk"
            f" that cites his ruling {ACCEPT_RULING}"
        )
    return ""


def owed(data: dict, key: str) -> str:
    """What its builder owes at its head, which the rework and red stages name and a builder's WAITING or ASK answers: the rework to his words, else its red()."""
    if rework := data["prs"][key]["rework"]:
        return f'being reworked to his words "{rework["words"]}"'
    return red(data, key)


def conflicts_with(pr: dict) -> str:
    """What GitHub reads the PR conflicting with: the trunk of its native GitHub stack, onto which GitHub test-merges it with every PR below it rather than onto its base branch, so only a restack of the whole stack onto that trunk clears it; its base branch for a PR in none."""
    return f"{pr['trunk']}, the trunk of its native stack" if pr["trunk"] else f"its base {pr['base']}"


def waited(data: dict, job_id: str, line: str) -> dict:
    """What a builder's WAITING or ASK line answers, kept on its job as the drive takes it: the step each open PR the job carries owes then, by owed(), its rework to his words or its red, and each PR of another job's and each release its words name, never one waiting on a part the job carries, by state.unlooped, as the PR a staging tester tests or the job's own PR waiting on its own staging test, which waits on the job in turn, and each question card of his a WAITING's first line's `waits on <cards>` names, the builder's own question included, or each card of his the job's open questions are drawn on as an ASK ends its run, that holds then, so a PR that step owes wakes that builder once per step until he answers, while a card cited on any other line, such as DESIGN:, is no wait of its step, holding only the landing of a PR whose DESIGN: line cites it, by state.part_cards."""
    job = data["jobs"][job_id]
    own = [key for key, pr in data["prs"].items() if pr["url"] in job["prs"]]
    declared = state.WAITING_LINE.match(line)
    asked = view.stored_waits(data, view.open_cards(job)) if re.match(r"ASK\b", line) else [card for card in view.stored_waits(data, state.BUILT_CARD.findall(declared["cards"])) if view.drawn_on(data, card)] if declared else []
    return {
        "owed": {key: step for key in own if data["prs"][key]["state"] == "open" and (step := owed(data, key))},
        "waits": [wait for wait in state.unlooped(data, job_id, [*state.named_waits(data, job["order"], line), *asked]) if wait not in own and view.holds(data, wait, None)],
    }


def answered(data: dict, key: str, step: str) -> list[str]:
    """What the builder answering for the PR waits on by its last WAITING or ASK, while that line answered this same step the PR owes, by owed(), and each PR, release and card of his it named still holds it, by onward(), so the PR wakes that builder again only once one of them ends, his answer closing the card, his new words reworking it, or its red changing."""
    if not (owner := owner_job(data, key)):
        return []
    job = data["jobs"][owner]
    if not job["waited"] or job["waited"]["owed"].get(key) != step:
        return []
    waits = job["waited"]["waits"]
    return waits if set(waits) <= set(onward(data, owner, None)) else []


def blocked(data: dict, key: str, step: str, waits: list[str], cards: dict | None) -> Stage:
    """A PR owing its builder a step, a rework to his words or a red, whose builder waits on the PRs, releases and question cards of his that let it take that step: each PR and card named by its link, owed by him while an end of them is his to move by asked(), which it names and links too, else by the session mover() names."""
    mine = asked(data, key, waits, cards)
    return Stage("blocked", HIM if mine else mover(data, key, waits, cards), f"{step}, and its builder waits on {', '.join(fix_words(data, wait, cards) for wait in dict.fromkeys([*waits, *mine]))}")


def fix_words(data: dict, wait: str, cards: dict | None) -> str:
    if state.card_wait(data, wait):
        drawn = next((question["card"] for _, question in view.drawn_on(data, wait) if question["card"]), None)
        return f"his answer on {view.link('his question card', drawn) if drawn else 'his question card'}"
    if wait not in data["prs"]:
        return wait
    pr = data["prs"][wait]
    asked = f", which waits on his Approve on {view.link('his card', card)}" if (card := view.tap_card(pr, cards)) else ""
    return view.pr_link(pr) + asked


def grading(pr: dict) -> str:
    return f"its checks run at {pr['head'][:7]}" if pr["checks"] == "pending" else ""


def behind(data: dict, key: str, cards: dict, now: Stage, under: dict[str, str | None] | None = None, stacks: dict[str, tuple[str, ...]] | None = None) -> Stage:
    """The PR's stage now, unless another PR his Approve lands it with, by land.landed_with() bottom first, holds it: one under it that GitHub reads conflicting, with what conflicts_with() names, which every PR above it waits on whatever its own row reads, since none of them lands before it and their builders can fix nothing until its owner settles it, or, while it reads ready, one that does not by its own row; then that PR's stage, seen from this one, whose owner owes the step. A stage someone answers on the PR itself stands: his card to draw, a lander's try, his hold and a rework to his words."""
    if now.name in OWN_STEP:
        return now
    under = land.unders(data) if under is None else under
    stacks = land.landing_stacks(data, under) if stacks is None else stacks
    below = land.chain(under, key)
    for member in land.landed_with(data, under, stacks, key):
        conflicts = member in below and data["prs"][member]["conflict"]
        if member != key and (conflicts or now.name == "ready") and (other := own(data, member, cards)).name != "ready":
            mates = land.together(data, stacks, member)
            where = "below it in its stack" if member in below else "on his card with it" if key in mates else f"on his card with {mates[0]}"
            base = f" conflicts with {conflicts_with(data['prs'][member])}," if conflicts else ","
            return Stage("behind", other.owner, f"{member} {where}{base} {other.name}: {other.why}")
    return now


def live(data: dict, pr: dict) -> bool:
    """Whether the PR fixes a bug hurting someone now, so a card asking him is asked again every EVERY of its stage."""
    return any(state.hurts_now(data, data["orders"][order_id]) for order_id in state.served_orders(data, pr))


def owner_job(data: dict, key: str) -> str | None:
    """The job that answers for the PR now: the one its row names, which its stage line's tag prints and drive pr add --job, drive job set --pr and a superseded fold move, whose finished or dead lane drive pr wake replaces with a fresh builder; None when that job is stopped, so no wake starts work he called off and the session holding its order closes the PR or moves it to a live job."""
    job_id = data["prs"][key]["job"]
    return None if data["jobs"][job_id]["state"] == "stopped" else job_id


def entered(kept: dict | None, now: Stage) -> bool:
    """Whether the PR entered a stage or its owner changed, as a blocked PR's does once the PR fixing it waits on his Approve or stops waiting, which redraws his card."""
    return not kept or (kept["name"], kept["owner"]) != (now.name, now.owner)


def overdue(data: dict, pr: dict, now: Stage, moment: datetime) -> bool:
    """Whether the stage's deadline passed since it began or its owner was last woken: every stage but behind, whose PR below names its owner, and one he owes only while it asks him on his card, held or card, and the PR fixes a live bug."""
    every = EVERY.get(now.name)
    if every is None or now.owner == HIM and (now.name not in ASKS_HIM or not live(data, pr)):
        return False
    kept = pr["stage"]
    return moment - state.parse_time(kept["woke"] or kept["since"]) >= every


def stamp(pr: dict, now: Stage, moment: datetime) -> None:
    pr["stage"] = {"name": now.name, "owner": now.owner, "why": now.why, "since": state.stamp(moment), "woke": None, "staging": None}


def kept(pr: dict, now: Stage) -> None:
    """Keep what the PR's stage says now on its row beside the since and woke its deadlines read; drive status and every view.build surface put on stage.of's stage now through shown()."""
    pr["stage"].update(owner=now.owner, why=now.why)


def shown(data: dict, cards: dict | None, keys: list[str]) -> None:
    """Put on each open PR of the drive the keys name, as loaded, never saved, the stage stage.of names now from the watch's last GitHub read, its since kept while the watch kept that stage, so drive status, drive pr set and every view.build surface, the dashboard, the status page and his catch-up, show what a decision made now reads."""
    under = land.unders(data)
    stacks = land.landing_stacks(data, under)
    for key in keys:
        pr = data["prs"][key]
        now = of(data, key, cards, under, stacks)
        since = pr["stage"]["since"] if pr["stage"] and pr["stage"]["name"] == now.name else None
        pr["stage"] = {"name": now.name, "owner": now.owner, "why": now.why, "since": since, "woke": None}


def heads(data: dict, said: str, order_id: str, cards: dict | None) -> str:
    """Each PR a builder's READY or PUSHED names at the head GitHub held in the watch's last read, with its stage there, and each commit the line names that none of them is at, so a line relayed from it never carries a head GitHub no longer holds."""
    keys = state.named_prs(data, order_id, said)
    parts = [f"{key} is at {data['prs'][key]['head'][:7]} on GitHub, {(now := of(data, key, cards)).name}, owed by {now.owner}: {now.why}" for key in keys if data["prs"][key]["state"] == "open"]
    stale = [sha for sha in NAMED_HEAD.findall(said) if not any(data["prs"][key]["head"].startswith(sha) for key in keys)]
    parts += [f"GitHub holds none of them at {', '.join(stale)}"] if stale else []
    return "".join(f"; {part}" for part in parts)


def line(key: str, pr: dict, now: Stage) -> str:
    """The PR's stage as its owner and drive status read it."""
    since = f" since {pr['stage']['since']}" if pr["stage"] and pr["stage"]["name"] == now.name else ""
    return f"{key} {now.name}{since}, owed by {now.owner}: {now.why}"


def wake(name: str, key: str, text: str, job_id: str | None = None) -> None:
    """Run drive pr wake on the PR as its own process, outliving the watch pass that started it, so an Orca send or a builder's relaunch never holds the watch, to the job its stage names, else its builder; its output appended to the drive's wake log."""
    state.detach(name, ["pr", "wake", state.pr_url(key), "--text", text, *(["--job", job_id] if job_id else [])], WAKES_LOG)
