import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from drive import land, state, view

NOBODY = "nobody"
WATCH = "the watch"
LANDER = "the lander"
ORDER = "the session holding its order"
HIM = "him"
BUILDER = "its builder"
GRADERS = "Buildkite and forge-pr-reviewer"
WAITED = "what it waits on"
ACCEPT_RULING = '2bb0472, "you have to accept the risk stop just pretendinf urblocked"'
UNLANDED_SILENT = "no outcome: its process ended before it said whether it queued anything"
WAKES_LOG = "wakes.log"
NAMED_HEAD = re.compile(r"\bat ([0-9a-f]{7,40})\b")
ASKS_HIM = ("held", "card")
OWN_STEP = ("uncarded", "landing", "unlanded", "held", "rework", "merged", "closed")
EVERY = {
    "landing": timedelta(minutes=90),
    "unlanded": timedelta(minutes=30),
    "held": timedelta(minutes=30),
    "rework": timedelta(minutes=30),
    "red": timedelta(minutes=30),
    "grading": timedelta(minutes=60),
    "card": timedelta(minutes=30),
    "ready": timedelta(minutes=30),
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
    """Every rule of own past the draw of his card: his Hold, the hold label and a rework to his words before a landing, which stands only while land.stopping() finds nothing stopping it, so a PR Trunk's merge queue holds reads what holds it the moment his words under its card take back his Approve, while land.halt takes it out of the queue."""
    pr = data["prs"][key]
    if pr["state"] != "open":
        return Stage(pr["state"], NOBODY, f"it is {pr['state']}")
    if stops := stopped(pr):
        return Stage("held", HIM, stops)
    if pr["rework"]:
        return Stage("rework", BUILDER, f'being reworked to his words "{pr["rework"]["words"]}"')
    if land.landing(pr) and not land.stopping(data, key):
        return Stage("landing", LANDER, landing_why(pr))
    if ended := unlanded(data, pr):
        return Stage("unlanded", ORDER, ended)
    if failing := red(key, pr):
        if waits := answered(data, key, failing):
            return blocked(data, failing, waits, cards)
        return Stage("red", BUILDER, failing)
    if holds := held(data, pr):
        return Stage("held", HIM, holds)
    if state.ships_untapped(data, key):
        return waiting(data, pr, cards) or Stage("ready", LANDER, f"its builder reported it and no check fails at {pr['head'][:7]}, so the lander merges it with --admin while any still run")
    if pending := grading(pr):
        return Stage("grading", GRADERS, pending)
    if (flaky := land.flaked(pr)) and data["prs"][flaky["fix"]]["state"] == "open":
        return Stage(
            "waiting",
            WAITED,
            f"Trunk ejected it from the merge queue at {pr['head'][:7]} on its flaky test {flaky['test']}; the lander requeues that commit once {flaky['fix']}, that test's fix, merges",
        )
    if state.cleared(pr):
        after = f"; the check against his words objects, for after the merge: {named}" if (named := state.against(pr)) else ""
        unread = f"; its lander's last try ended {lander['outcome']}" if (lander := pr["lander"]) and lander["head"] == pr["head"] and (lander["outcome"] or "").startswith(land.UNREAD) else ""
        return waiting(data, pr, cards) or Stage("ready", LANDER, (f'green, its exempt class "{pr["exempt"]}" lands it' if state.exempted(pr) else f"green and approved by him at {pr['tap']['head'][:7]}{after}") + unread)
    if carded(pr):
        objection = f"; at {pr['head'][:7]} the check against his words objects: {named}" if (named := state.against(pr)) else ""
        return Stage("card", HIM, f"green, waits on his Approve{objection}")
    return Stage("author", BUILDER, "green; its author or its own builder lands it with no tap")


def waiting(data: dict, pr: dict, cards: dict) -> Stage | None:
    """The stage of a PR the lander would land now while a PR, job or release of the drive it waits on still holds, which every ready rule of alone reads first, so the lander lands none of them until the watch frees it; None once nothing it waits on holds."""
    if standing := view.holding(data, pr["waits"], cards):
        return Stage("waiting", WAITED, f"it waits on {', '.join(standing)}, and the watch frees it once each ends")
    return None


def carded(pr: dict) -> bool:
    """Whether the PR waits on his card for its landing: it wants his tap and no exempt class lands it."""
    return pr["wants_tap"] and not state.exempted(pr)


def landing_why(pr: dict) -> str:
    lander = pr["lander"]
    if lander["outcome"] == land.QUEUED:
        flaky = pr["flaky"] if pr["flaky"] and pr["flaky"]["requeued"] and pr["flaky"]["head"] == lander["head"] else None
        requeued = f", requeued once {flaky['fix']}, the fix of its flaky test {flaky['test']}, merged" if flaky else ""
        taken = f"; pid {dequeuer['pid']} takes it out of the queue since {dequeuer['at']}, and it lands again after that" if (dequeuer := lander.get("dequeue")) else ""
        return f"queued for merge at {lander['head'][:7]} since {lander['at']}{requeued}{taken}"
    return f"lander pid {lander['pid']} lands {lander['head'][:7]}, started {lander['at']}"


def unlanded(data: dict, pr: dict) -> str | None:
    """How a lander's try at the PR's head ended without landing it, or None when no try at this head stands: none ran on it, Trunk holds it, GitHub refused its merge for a conflict with its base, which red() owes its builder, GitHub refused() the merge Trunk's queue took, so the lander lands it again one PR at a time, or ejected it on a flaky test whose fix it waits on, by ejection(), what let it run moved since, nothing clearing it now while it waits on his card, as after drive pr set --no-exempt --tap, or his tap made after the try started, or what the try waited on moved since, a PR below it that is cleared or merged now, a newer install's save that ended it before it queued anything, a hold of his that it or halt() stopped it on, which held() reads while it stands, or the lander's own failure, no fresh worktree of the default branch to land from, such as when GitHub did not answer, stack-enqueue failing on its own, or a GitHub read that failed before it queued or merged anything, so the lander tries again."""
    lander = pr["lander"]
    if not lander or lander["head"] != pr["head"] or land.running(pr) or land.refused(pr) or land.conflicted(pr):
        return None
    if carded(pr) and not state.cleared(pr) or pr["tap"] and state.parse_time(pr["tap"]["at"]) > state.parse_time(lander["at"]):
        return None
    if lander["outcome"] == land.QUEUED:
        return ejection(data, pr) if pr["ejected"] else None
    outcome = lander["outcome"] or UNLANDED_SILENT
    if outcome.startswith(land.RETRIED):
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


def stopped(pr: dict) -> str:
    """What stops the PR whatever its checks read: his Hold or the hold label, on which his card offers no Approve, so a red PR under one wakes no builder."""
    if (pr["tap"] or {}).get("label") == "Hold":
        return f"held by his Hold of {pr['tap']['at']}"
    if pr["hold_label"]:
        return "held by the hold label on GitHub"
    return ""


def held(data: dict, pr: dict) -> str:
    """What of his holds the PR: what stops it, or a question card of his open on the work of any job carrying it whose `Holds:` line holds it, by land.pr_cards; a red PR one of his cards holds reads red, so its builder keeps it green while he decides."""
    if stops := stopped(pr):
        return stops
    if asking := land.pr_cards(data, pr):
        return f"his cards {', '.join(asking)} on its work are open"
    return ""


def red(key: str, pr: dict) -> str:
    """What its builder must fix at its head: failing checks, a conflict GitHub reads or refused the lander's merge for, named as conflicts_with() names it, with the restack that clears one in a native GitHub stack, a draft, changes a reviewer asked for, or, in a repository whose rules require an approving review before merging, a forge-pr-reviewer that finished on a green head and approved nothing, which holds it until each finding is fixed or its risk accepted."""
    head = pr["head"][:7]
    if pr["checks"] == "fail":
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


def conflicts_with(pr: dict) -> str:
    """What GitHub reads the PR conflicting with: the trunk of its native GitHub stack, onto which GitHub test-merges it with every PR below it rather than onto its base branch, so only a restack of the whole stack onto that trunk clears it; its base branch for a PR in none."""
    return f"{pr['trunk']}, the trunk of its native stack" if pr["trunk"] else f"its base {pr['base']}"


def waited(data: dict, job_id: str, line: str) -> dict:
    """What a builder's WAITING line answers, kept on its job as the drive takes it: the red of each open PR the job carries, and each PR of another job's and each release its words name and each question card of his its first line's `waits on <cards>` names, the builder's own question included, that holds then, so a red only his answer fixes wakes that builder once per red until he answers, while a card cited on any other line, such as DESIGN:, holds nothing."""
    job = data["jobs"][job_id]
    own = [key for key, pr in data["prs"].items() if pr["url"] in job["prs"]]
    declared = state.WAITING_LINE.match(line)
    asked = [card for card in view.stored_waits(data, state.BUILT_CARD.findall(declared["cards"])) if view.drawn_on(data, card)] if declared else []
    return {
        "red": {key: failing for key in own if data["prs"][key]["state"] == "open" and (failing := red(key, data["prs"][key]))},
        "waits": [wait for wait in [*state.named_waits(data, job["order"], line), *asked] if wait not in own and view.holds(data, wait, None)],
    }


def answered(data: dict, key: str, failing: str) -> list[str]:
    """What the builder answering for a red PR waits on by its last WAITING, while that line answered this same red and each PR, release and card of his it named still holds, so the PR wakes that builder again only once one of them ends, his answer closing the card, or the red changes."""
    if not (owner := owner_job(data, key)):
        return []
    job = data["jobs"][owner]
    if job["state"] != "waiting" or not (job["said"] or "").startswith("WAITING") or not job["waited"] or job["waited"]["red"].get(key) != failing:
        return []
    waits = job["waited"]["waits"]
    return waits if all(wait in job["waits"] and view.holds(data, wait, None) for wait in waits) else []


def blocked(data: dict, failing: str, waits: list[str], cards: dict | None) -> Stage:
    """A red PR whose builder waits on the PRs, releases and question cards of his that fix it: each PR and card named by its link, owed by him while one of them is his question card or waits on his Approve on its drawn card, which it links, else by what it waits on, whose own stage wakes its owner."""
    asks = any(state.card_wait(data, wait) or wait in data["prs"] and view.tap_card(data["prs"][wait], cards) for wait in waits)
    return Stage("blocked", HIM if asks else WAITED, f"{failing}, and its builder waits on {', '.join(fix_words(data, wait, cards) for wait in waits)}")


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
    """Whether the stage's deadline passed since it began or its owner was last woken: one asking him only while the PR fixes a live bug."""
    every = EVERY.get(now.name)
    if every is None or now.name in ASKS_HIM and not live(data, pr):
        return False
    kept = pr["stage"]
    return moment - state.parse_time(kept["woke"] or kept["since"]) >= every


def stamp(pr: dict, now: Stage, moment: datetime) -> None:
    pr["stage"] = {"name": now.name, "owner": now.owner, "why": now.why, "since": state.stamp(moment), "woke": None}


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


def wake(name: str, key: str, text: str) -> None:
    """Run drive pr wake on the PR as its own process, outliving the watch pass that started it, so an Orca send or a builder's relaunch never holds the watch; its output appended to the drive's wake log."""
    state.detach(name, ["pr", "wake", state.pr_url(key), "--text", text], WAKES_LOG)
